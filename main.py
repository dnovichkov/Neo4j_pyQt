import sys
import os
import json
from functools import partial
import tempfile

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QComboBox, QLabel, QVBoxLayout,
    QWidget, QAction, QFileDialog, QMessageBox, QDialog
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import (
    QUrl, pyqtSlot, QObject, QRunnable, QThreadPool, pyqtSignal
)
from pyvis.network import Network

from dialogs import NodeDialog, RelationshipDialog, NewNodeDialog, NewRelationshipDialog, ConnectionDialog
from neo_4j_client import Neo4jClient

# ---------------------------
# Логирование
# ---------------------------

from log_utils import logger

# ---------------------------
# Конфиг
# ---------------------------
CONFIG_FILE = "config.json"


def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_cfg = {
            "uri": "bolt://localhost:7687",
            "user": "neo4j",
            "password": "testtest"
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_cfg, f, indent=4, ensure_ascii=False)
        return default_cfg
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def get_node_types_query():
    # исправлено: exists(...) → IS NOT NULL
    return "MATCH (n) WHERE n.`тип` IS NOT NULL RETURN DISTINCT n.`тип` AS t"


def _js_bridge_script():
    # Возвращает готовый блок JS + закрывающий </body>.
    # Встраивается через html.replace("</body>", _js_bridge_script())
    return """
<script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
if (typeof QWebChannel === "function") {
  new QWebChannel(qt.webChannelTransport, function(channel) {
    window.bridge = channel.objects.bridge;
    if (typeof network !== "undefined" && network) {
      network.on("click", function(params) {
        try {
          if (params && params.nodes && params.nodes.length > 0) {
            bridge.onNodeClicked("node", params.nodes[0].toString());
          } else if (params && params.edges && params.edges.length > 0) {
            bridge.onNodeClicked("edge", params.edges[0].toString());
          }
        } catch (e) { console.error("Bridge error:", e); }
      });
    }
  });
}
</script>
</body>
"""


# ---------------------------
# Worker для выполнения задач
# ---------------------------
class WorkerSignals(QObject):
    result = pyqtSignal(object)   # {'task': name, 'result': ...}
    error = pyqtSignal(object)    # {'task': name, 'error': exception}
    finished = pyqtSignal()


class Worker(QRunnable):
    def __init__(self, fn, task_name=None, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.task_name = task_name
        self.signals = WorkerSignals()

    def run(self):
        try:
            res = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit({'task': self.task_name, 'result': res})
        except Exception as e:
            logger.exception("Worker task %s raised an exception", self.task_name)
            self.signals.error.emit({'task': self.task_name, 'error': e})
        finally:
            self.signals.finished.emit()


# ---------------------------
# Мост JS ↔ Python
# ---------------------------
class Bridge(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)

    @pyqtSlot(str, str)
    def onNodeClicked(self, element_type, element_id):
        main = self.parent()
        if element_type == "node":
            nodes, _ = main.client.get_graph()
            node = next((n for n in nodes if str(n.get("id")) == str(element_id)), None)
            if node:
                dlg = NodeDialog(
                    node_id=node["id"],
                    node_label=node["label"],
                    node_props=node["properties"],
                    parent=main
                )
                if dlg.exec_() == QDialog.Accepted:
                    data = dlg.node_data
                    # обновляем только свойства; смену метки в данном примере не записываем как label/Label
                    main.submit_task(
                        lambda: main.client.update_node_properties(node["id"], data["properties"]),
                        'update_node'
                    )
        elif element_type == "edge":
            _, rels = main.client.get_graph()
            rel = next((r for r in rels if str(r.get("id")) == str(element_id)), None)
            if rel:
                dlg = RelationshipDialog(rel_type=rel["type"], rel_props=rel["properties"], parent=main)
                if dlg.exec_() == QDialog.Accepted:
                    data = dlg.rel_data
                    main.submit_task(
                        lambda: main.client.update_relationship_properties(rel["id"], data["properties"]),
                        'update_rel'
                    )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Neo4j PyQt App (async)")
        # адекватный стартовый размер + минимальный
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        # загружаем конфиг и подключаемся
        self.config = load_config()
        self.client = Neo4jClient(
            uri=self.config["uri"],
            user=self.config["user"],
            password=self.config["password"]
        )

        # WebView
        self.view = QWebEngineView()
        central_layout = QVBoxLayout()
        central_layout.addWidget(self.view)
        central_widget = QWidget()
        central_widget.setLayout(central_layout)
        self.setCentralWidget(central_widget)

        # JS Bridge
        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        # Toolbar
        toolbar = QToolBar("Фильтры")
        self.addToolBar(toolbar)
        toolbar.addWidget(QLabel("Фильтр по типу: "))
        self.filter_box = QComboBox()
        toolbar.addWidget(self.filter_box)
        self.filter_box.currentTextChanged.connect(self._reload_graph)

        add_node_btn = QAction("Создать узел", self)
        add_node_btn.triggered.connect(self._create_node)
        toolbar.addAction(add_node_btn)

        add_rel_btn = QAction("Создать отношение", self)
        add_rel_btn.triggered.connect(self._create_relationship)
        toolbar.addAction(add_rel_btn)

        # Меню
        menubar = self.menuBar()
        file_menu = menubar.addMenu("Файл")

        export_action = QAction("Экспортировать граф", self)
        export_action.triggered.connect(self._export_graph)
        file_menu.addAction(export_action)

        settings_action = QAction("Настройки подключения", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        # Пул потоков
        self.pool = QThreadPool.globalInstance()

        # Инициализация UI
        self._populate_filters()
        self._load_graph_async()

    # ---------- Helpers: задачи в пул ----------
    def submit_task(self, fn, task_name=None, *args, **kwargs):
        worker = Worker(partial(fn, *args, **kwargs), task_name=task_name)
        worker.signals.result.connect(self._on_task_result)
        worker.signals.error.connect(self._on_task_error)
        self.pool.start(worker)
        return worker

    def _on_task_result(self, payload):
        task = payload.get('task')
        result = payload.get('result')
        logger.debug("Task finished: %s", task)
        if task == 'get_graph':
            nodes, rels = result
            self._apply_graph_to_view(nodes, rels)
        elif task == 'get_types':
            self._apply_filters(result)
        else:
            if task in ('add_node', 'add_rel', 'update_node', 'update_rel'):
                self._populate_filters_async()
                self._load_graph_async()

    def _on_task_error(self, payload):
        task = payload.get('task')
        err = payload.get('error')
        logger.exception("Error in task %s: %s", task, err)
        QMessageBox.critical(self, f"Ошибка в задаче {task}", str(err))

    # ---------- Фильтры ----------
    def _populate_filters(self):
        try:
            with self.client.driver.session() as session:
                result = session.run(get_node_types_query())
                types = [rec["t"] for rec in result if rec["t"]]
            types = ["Все"] + sorted(set(types))
        except Exception:
            types = ["Все"]
        self.filter_box.clear()
        self.filter_box.addItems(types)

    def _populate_filters_async(self):
        def task():
            with self.client.driver.session() as session:
                result = session.run(get_node_types_query())
                return [rec["t"] for rec in result if rec["t"]]
        self.submit_task(task, 'get_types')

    def _apply_filters(self, types):
        types = ["Все"] + sorted(set(types))
        self.filter_box.blockSignals(True)
        self.filter_box.clear()
        self.filter_box.addItems(types)
        self.filter_box.blockSignals(False)

    # ---------- Граф ----------
    def _load_graph_async(self):
        self.submit_task(self.client.get_graph, 'get_graph')

    def _apply_graph_to_view(self, nodes, rels, selected_type=None):
        try:
            selected_type = selected_type or self.filter_box.currentText()
            if selected_type and selected_type != "Все":
                nodes = [n for n in nodes if n["properties"].get("тип") == selected_type]
                node_ids = {n["id"] for n in nodes}
                rels = [r for r in rels if r["from"] in node_ids and r["to"] in node_ids]

            net = Network(height="750px", width="100%", directed=True)
            for n in nodes:
                net.add_node(n["id"], label=n.get("label", n["id"]), title=str(n.get("properties", {})))
            for r in rels:
                arrows = "to" if r.get("direction", "->") == "->" else "from" if r.get(
                    "direction") == "<-" else "to,from"
                net.add_edge(r["from"], r["to"], label=r["type"], title=str(r.get("properties", {})), arrows=arrows,
                             id=r["id"])

            # Создаём временный файл
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
            tmp_file.close()
            net.write_html(tmp_file.name, notebook=False)

            # Добавляем JS-мост
            with open(tmp_file.name, "r", encoding="utf-8") as f:
                html = f.read()
            html = html.replace("</body>", _js_bridge_script())
            with open(tmp_file.name, "w", encoding="utf-8") as f:
                f.write(html)

            # Загружаем в WebEngineView
            self.view.load(QUrl.fromLocalFile(tmp_file.name))
            self._tmp_graph_file = tmp_file.name

        except Exception as e:
            logger.exception("Error applying graph to view: %s", e)

    def _reload_graph(self, _selected_type):
        self._load_graph_async()

    # ---------- Создание/редактирование ----------
    def _create_node(self):
        dlg = NewNodeDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            self.submit_task(lambda: self.client.add_node(data["label"], data["properties"]), 'add_node')

    def _create_relationship(self):
        nodes, _ = self.client.get_graph()
        dlg = NewRelationshipDialog(nodes, self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            self.submit_task(
                lambda: self.client.add_relationship(
                    data["from"], data["to"], data["type"], data["direction"], data["properties"]
                ),
                'add_rel'
            )

    # ---------- Экспорт ----------
    def _export_graph(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить граф как HTML", "", "HTML Files (*.html)")
        if path:
            try:
                nodes, rels = self.client.get_graph()
                net = Network(height="750px", width="100%", directed=True)
                for n in nodes:
                    net.add_node(n["id"], label=n.get("label", n["id"]), title=str(n.get("properties", {})))
                for r in rels:
                    net.add_edge(r["from"], r["to"], label=r["type"], title=str(r.get("properties", {})), id=r["id"])
                net.write_html(path, notebook=False)
            except Exception as e:
                logger.exception("Export error: %s", e)
                QMessageBox.critical(self, "Ошибка экспорта", str(e))

    # ---------- Настройки подключения ----------
    def _open_settings(self):
        dlg = ConnectionDialog(self.config, self)
        if dlg.exec_() == QDialog.Accepted:
            cfg = dlg.get_config()
            save_config(cfg)
            self.config = cfg
            try:
                self.client.close()
            except Exception:
                pass
            self.client = Neo4jClient(cfg["uri"], cfg["user"], cfg["password"])
            self._populate_filters()
            self._load_graph_async()
            QMessageBox.information(self, "Успех", "Подключение обновлено")

    # ---------- Закрытие ----------
    def closeEvent(self, event):
        try:
            self.client.close()
        except Exception:
            pass
        # удаляем временный HTML при выходе
        if hasattr(self, '_tmp_graph_file') and self._tmp_graph_file and os.path.exists(self._tmp_graph_file):
            try:
                os.remove(self._tmp_graph_file)
            except OSError:
                pass
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    # на всякий случай центрируем окно
    try:
        screen_geom = QApplication.primaryScreen().availableGeometry()
        x = (screen_geom.width() - w.width()) // 2
        y = (screen_geom.height() - w.height()) // 2
        w.move(x, y)
    except Exception:
        pass
    w.show()
    sys.exit(app.exec_())
