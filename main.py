import sys
import os
import json
import tempfile
import uuid
import logging
from functools import partial

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QComboBox, QLabel, QVBoxLayout,
    QWidget, QAction, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QLineEdit, QPushButton, QHBoxLayout
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import (
    QUrl, pyqtSlot, QObject, QRunnable, QThreadPool, pyqtSignal, Qt
)
from pyvis.network import Network
from neo4j import GraphDatabase


# ---------------------------
# Логирование
# ---------------------------
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


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
# Neo4j клиент
# ---------------------------
class Neo4jClient:
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", password="testtest"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        try:
            self.driver.close()
        except Exception:
            pass

    def get_graph(self):
        with self.driver.session() as session:
            nodes_result = session.run("MATCH (n) RETURN n")
            nodes = []
            for record in nodes_result:
                n = record["n"]
                props = dict(n.items())
                node_uuid = props.get("uuid") or str(n.id)
                labels = list(getattr(n, "labels", []))
                label = labels[0] if labels else props.get("label") or node_uuid
                nodes.append({
                    "id": node_uuid,
                    "label": label,
                    "properties": props
                })

            rels_result = session.run("MATCH (a)-[r]->(b) RETURN r, a, b")
            rels = []
            for record in rels_result:
                r = record["r"]
                a = record["a"]
                b = record["b"]
                r_props = dict(r.items())
                rel_uuid = r_props.get("uuid") or str(r.id)
                from_uuid = dict(a.items()).get("uuid") or str(a.id)
                to_uuid = dict(b.items()).get("uuid") or str(b.id)
                rels.append({
                    "id": rel_uuid,
                    "from": from_uuid,
                    "to": to_uuid,
                    "type": r.type,
                    "properties": r_props,
                    "direction": "->"
                })
        logger.debug("Loaded %d nodes and %d relationships", len(nodes), len(rels))
        return nodes, rels

    def add_node(self, label, properties):
        with self.driver.session() as session:
            node_uuid = str(uuid.uuid4())
            props = dict(properties or {})
            props["uuid"] = node_uuid
            safe_label = "".join(ch for ch in (label or "Node") if ch.isalnum() or ch == "_") or "Node"
            query = f"CREATE (n:{safe_label}) SET n += $props RETURN n"
            logger.debug("Creating node: label=%s props=%s", safe_label, props)
            result = session.run(query, props=props)
            return list(result)

    def add_relationship(self, from_uuid, to_uuid, r_type, direction, properties):
        with self.driver.session() as session:
            rel_uuid = str(uuid.uuid4())
            props = dict(properties or {})
            props["uuid"] = rel_uuid
            safe_type = "".join(ch for ch in (r_type or "REL") if ch.isalnum() or ch == "_") or "REL"
            # направление в pyvis отображаем стрелками; в БД создаём (a)-[r]->(b)
            if direction == "<-":
                from_uuid, to_uuid = to_uuid, from_uuid
            query = (
                f"MATCH (a {{uuid:$from_uuid}}), (b {{uuid:$to_uuid}}) "
                f"CREATE (a)-[r:{safe_type}]->(b) SET r += $props RETURN r"
            )
            logger.debug("Creating relationship %s: %s -> %s, props=%s", safe_type, from_uuid, to_uuid, props)
            result = session.run(query, from_uuid=from_uuid, to_uuid=to_uuid, props=props)
            return list(result)

    def update_node_properties(self, node_uuid, properties):
        with self.driver.session() as session:
            query = "MATCH (n) WHERE n.uuid=$nid SET n += $props RETURN n"
            logger.debug("Updating node %s props=%s", node_uuid, properties)
            session.run(query, nid=node_uuid, props=properties)

    def update_relationship_properties(self, rel_uuid, properties):
        with self.driver.session() as session:
            query = "MATCH ()-[r]->() WHERE r.uuid=$rid SET r += $props RETURN r"
            logger.debug("Updating relationship %s props=%s", rel_uuid, properties)
            session.run(query, rid=rel_uuid, props=properties)


# ---------------------------
# PropertyEditor (с кнопкой удаления строк)
# ---------------------------
class PropertyEditor(QWidget):
    def __init__(self, properties=None):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()
        self.layout.addLayout(self.form_layout)
        self.fields = []  # (key_edit, val_edit, row_widget)

        if properties:
            for k, v in properties.items():
                self.add_field(k, v)

        add_btn = QPushButton("Добавить поле")
        add_btn.clicked.connect(self._add_field_clicked)
        self.layout.addWidget(add_btn)

    def _add_field_clicked(self):
        self.add_field()

    def add_field(self, key="", value=""):
        key_edit = QLineEdit(str(key))
        val_edit = QLineEdit(str(value))
        remove_btn = QPushButton("✕")
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(key_edit)
        row_layout.addWidget(val_edit)
        row_layout.addWidget(remove_btn)
        self.form_layout.addRow(row_widget)
        self.fields.append((key_edit, val_edit, row_widget))

        remove_btn.clicked.connect(lambda: self._remove_field(row_widget))

    def _remove_field(self, row_widget):
        for i, (k_edit, v_edit, rw) in enumerate(self.fields):
            if rw is row_widget:
                self.form_layout.removeRow(self.form_layout.indexOf(rw))
                rw.setParent(None)
                self.fields.pop(i)
                return

    def get_properties(self):
        out = {}
        for k_edit, v_edit, _ in self.fields:
            k = k_edit.text().strip()
            if not k:
                continue
            out[k] = v_edit.text()
        return out


# ---------------------------
# Диалоги
# ---------------------------
class NodeDialog(QDialog):
    """Редактирование существующего узла"""
    def __init__(self, node_id, node_label=None, node_props=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Узел {node_id}")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)

        layout = QVBoxLayout(self)

        self.editor = PropertyEditor(node_props or {})
        layout.addWidget(self.editor)

        layout.addWidget(QLabel("Метка узла:"))
        self.label_edit = QLineEdit(node_label or "")
        layout.addWidget(self.label_edit)

        btns = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_cancel = QPushButton("Отмена")
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

        btn_save.clicked.connect(self._on_save_clicked)
        btn_cancel.clicked.connect(self.reject)

    def _on_save_clicked(self):
        self.node_data = {
            "label": self.label_edit.text().strip(),
            "properties": self.editor.get_properties()
        }
        self.accept()


class RelationshipDialog(QDialog):
    """Редактирование существующего отношения"""
    def __init__(self, rel_type, rel_props=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Редактировать связь {rel_type}")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)

        layout = QVBoxLayout(self)

        self.editor = PropertyEditor(rel_props or {})
        layout.addWidget(self.editor)

        btns = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_cancel = QPushButton("Отмена")
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

        btn_save.clicked.connect(self._on_save_clicked)
        btn_cancel.clicked.connect(self.reject)

    def _on_save_clicked(self):
        self.rel_data = {"properties": self.editor.get_properties()}
        self.accept()


class NewNodeDialog(QDialog):
    """Создание нового узла (с предпросмотром PyVis)"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать новый узел")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Метка узла:"))
        self.label_edit = QLineEdit()
        layout.addWidget(self.label_edit)

        self.editor = PropertyEditor()
        layout.addWidget(self.editor)

        layout.addWidget(QLabel("Предпросмотр узла:"))
        self.preview_view = QWebEngineView()
        layout.addWidget(self.preview_view)

        btns = QHBoxLayout()
        btn_create = QPushButton("Создать")
        btn_cancel = QPushButton("Отмена")
        btns.addWidget(btn_create)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

        btn_create.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        self._last_preview = None
        self.label_edit.textChanged.connect(self.update_preview)
        # при желании можно подписаться на изменения полей; здесь обновляем при закрытии диалога
        self.update_preview()

    def get_data(self):
        return {
            "label": self.label_edit.text().strip(),
            "properties": self.editor.get_properties()
        }

    def update_preview(self):
        try:
            label = self.label_edit.text().strip() or "Node"
            props = self.editor.get_properties()
            net = Network(height="200px", width="100%", directed=True)
            net.add_node("preview", label=label, title=str(props))

            if self._last_preview and os.path.exists(self._last_preview):
                try:
                    os.remove(self._last_preview)
                except OSError:
                    pass

            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
            tmp_file.close()
            net.write_html(tmp_file.name, notebook=False)
            self._last_preview = tmp_file.name
            self.preview_view.load(QUrl.fromLocalFile(tmp_file.name))
        except Exception as e:
            logger.exception("Preview error: %s", e)

    def closeEvent(self, event):
        if self._last_preview and os.path.exists(self._last_preview):
            try:
                os.remove(self._last_preview)
            except OSError:
                pass
        super().closeEvent(event)


class NewRelationshipDialog(QDialog):
    """Создание нового отношения"""
    def __init__(self, nodes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать новое отношение")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)

        layout = QVBoxLayout(self)

        self.from_box = QComboBox()
        self.to_box = QComboBox()
        for n in nodes:
            label = n.get("label") or n.get("id")
            uuid_val = n.get("properties", {}).get("uuid") or n.get("id")
            self.from_box.addItem(label, uuid_val)
            self.to_box.addItem(label, uuid_val)

        layout.addWidget(QLabel("От узла:"))
        layout.addWidget(self.from_box)
        layout.addWidget(QLabel("К узлу:"))
        layout.addWidget(self.to_box)

        layout.addWidget(QLabel("Тип отношения:"))
        self.type_edit = QLineEdit("REL_TYPE")
        layout.addWidget(self.type_edit)

        layout.addWidget(QLabel("Направление:"))
        self.direction_box = QComboBox()
        self.direction_box.addItems(["->", "<-", "двунаправленное"])
        layout.addWidget(self.direction_box)

        self.editor = PropertyEditor()
        layout.addWidget(self.editor)

        btns = QHBoxLayout()
        btn_create = QPushButton("Создать")
        btn_cancel = QPushButton("Отмена")
        btns.addWidget(btn_create)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

        btn_create.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

    def get_data(self):
        return {
            "from": self.from_box.currentData(),
            "to": self.to_box.currentData(),
            "type": (self.type_edit.text().strip() or "REL"),
            "direction": self.direction_box.currentText(),
            "properties": self.editor.get_properties()
        }


# ---------------------------
# Диалог настроек подключения
# ---------------------------
class ConnectionDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки подключения Neo4j")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)

        layout = QFormLayout(self)

        self.uri_edit = QLineEdit(config.get("uri", "bolt://localhost:7687"))
        self.user_edit = QLineEdit(config.get("user", "neo4j"))
        self.pass_edit = QLineEdit(config.get("password", ""))
        self.pass_edit.setEchoMode(QLineEdit.Password)

        layout.addRow("URI:", self.uri_edit)
        layout.addRow("Пользователь:", self.user_edit)
        layout.addRow("Пароль:", self.pass_edit)

        btns = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_cancel = QPushButton("Отмена")
        btns.addWidget(btn_save)
        btns.addWidget(btn_cancel)
        layout.addRow(btns)

        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

    def get_config(self):
        return {
            "uri": self.uri_edit.text().strip(),
            "user": self.user_edit.text().strip(),
            "password": self.pass_edit.text().strip()
        }


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


# ---------------------------
# Главное окно
# ---------------------------
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
                net.add_node(
                    n["id"],
                    label=n.get("label", n["id"]),
                    title=str(n.get("properties", {}))
                )
            for r in rels:
                arrows = (
                    "to" if r.get("direction", "->") == "->"
                    else "from" if r.get("direction") == "<-"
                    else "to,from"
                )
                net.add_edge(
                    r["from"], r["to"],
                    label=r["type"],
                    title=str(r.get("properties", {})),
                    arrows=arrows,
                    id=r["id"]  # важно: чтобы клик по ребру возвращал его id
                )

            file_path = os.path.abspath("graph.html")
            net.write_html(file_path, notebook=False)

            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()

            # Вставляем JS-мост
            html = html.replace("</body>", _js_bridge_script())
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html)

            self.view.load(QUrl.fromLocalFile(file_path))
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
        super().closeEvent(event)


# ---------------------------
# Точка входа
# ---------------------------
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
