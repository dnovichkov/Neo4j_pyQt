import sys
import os
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
from PyQt5.QtCore import QUrl, pyqtSlot, QObject, QRunnable, QThreadPool, pyqtSignal
from pyvis.network import Network
from neo4j import GraphDatabase

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def _js_bridge_script():
    return """
<script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
new QWebChannel(qt.webChannelTransport, function(channel) {
    window.bridge = channel.objects.bridge;
    network.on("click", function(params) {
        if(params.nodes && params.nodes.length > 0){
            bridge.onNodeClicked("node", params.nodes[0].toString());
        } else if(params.edges && params.edges.length > 0){
            bridge.onNodeClicked("edge", params.edges[0].toString());
        }
    });
});
</script>
</body>"""

def get_node_types_query():
    return "MATCH (n) WHERE n.`тип` IS NOT NULL RETURN DISTINCT n.`тип` as t"

# ---------------------------
# Worker для выполнения задач в пуле потоков
# ---------------------------
class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(object)
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
        self.driver.close()

    def get_graph(self):
        with self.driver.session() as session:
            nodes_result = session.run("MATCH (n) RETURN n")
            nodes = []
            for record in nodes_result:
                n = record["n"]
                props = dict(n.items())
                node_uuid = props.get("uuid") or str(n.id)
                labels = list(n.labels) if hasattr(n, 'labels') else []
                label = labels[0] if labels else node_uuid
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
                props = dict(r.items())
                rel_uuid = props.get("uuid") or str(r.id)
                from_uuid = dict(a.items()).get("uuid") or str(a.id)
                to_uuid = dict(b.items()).get("uuid") or str(b.id)
                rels.append({
                    "id": rel_uuid,
                    "from": from_uuid,
                    "to": to_uuid,
                    "type": r.type,
                    "properties": props,
                    "direction": "->"
                })
        logger.debug(f"Loaded {len(nodes)} nodes and {len(rels)} relationships from Neo4j")
        return nodes, rels

    def add_node(self, label, properties):
        with self.driver.session() as session:
            node_uuid = str(uuid.uuid4())
            props = properties.copy() if properties else {}
            props["uuid"] = node_uuid
            safe_label = "".join(ch for ch in (label or "Node") if ch.isalnum() or ch == "_") or "Node"
            query = f"CREATE (n:{safe_label}) SET n += $props RETURN n"
            logger.debug("Creating node with label=%s props=%s", safe_label, props)
            result = session.run(query, props=props)
            created = list(result)
            logger.debug("Node created: %s", created)
            return created

    def add_relationship(self, from_uuid, to_uuid, r_type, direction, properties):
        with self.driver.session() as session:
            rel_uuid = str(uuid.uuid4())
            props = properties.copy() if properties else {}
            props["uuid"] = rel_uuid
            safe_type = "".join(ch for ch in (r_type or "REL") if ch.isalnum() or ch == "_") or "REL"
            query = (
                f"MATCH (a {{uuid:$from_uuid}}), (b {{uuid:$to_uuid}}) "
                f"CREATE (a)-[r:{safe_type}]->(b) SET r += $props RETURN r"
            )
            logger.debug("Creating relationship from=%s to=%s type=%s props=%s", from_uuid, to_uuid, safe_type, props)
            result = session.run(query, from_uuid=from_uuid, to_uuid=to_uuid, props=props)
            created = list(result)
            logger.debug("Relationship created: %s", created)
            return created

    def update_node_properties(self, node_uuid, properties):
        with self.driver.session() as session:
            query = "MATCH (n) WHERE n.uuid=$nid SET n += $props RETURN n"
            logger.debug("Updating node %s with props=%s", node_uuid, properties)
            session.run(query, nid=node_uuid, props=properties)

    def update_relationship_properties(self, rel_uuid, properties):
        with self.driver.session() as session:
            query = "MATCH ()-[r]->() WHERE r.uuid=$rid SET r += $props RETURN r"
            logger.debug("Updating relationship %s with props=%s", rel_uuid, properties)
            session.run(query, rid=rel_uuid, props=properties)


# ---------------------------
# PropertyEditor
# ---------------------------
class PropertyEditor(QWidget):
    def __init__(self, properties=None):
        super().__init__()
        self.layout = QVBoxLayout()
        self.form_layout = QFormLayout()
        self.layout.addLayout(self.form_layout)
        self.setLayout(self.layout)
        self.fields = []  # list of tuples (key_edit, val_edit, row_widget)

        if properties:
            for k, v in properties.items():
                self.add_field(k, v)

        add_btn = QPushButton("Добавить поле")
        add_btn.clicked.connect(lambda: self._add_and_refresh())
        self.layout.addWidget(add_btn)

    def _add_and_refresh(self):
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

        def _remove():
            self._remove_field(row_widget)

        remove_btn.clicked.connect(_remove)

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
            v = v_edit.text()
            out[k] = v
        return out


# ---------------------------
# Диалоги узлов и связей
# ---------------------------
class NodeDialog(QDialog):
    def __init__(self, node_id, node_label=None, node_props=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Узел {node_id}")
        self.setModal(True)
        self.node_id = node_id
        layout = QVBoxLayout(self)

        props = node_props or {}
        self.editor = PropertyEditor(props)
        layout.addWidget(self.editor)

        self.label_edit = QLineEdit(node_label or "")
        layout.addWidget(QLabel("Метка узла:"))
        layout.addWidget(self.label_edit)

        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._on_save_clicked)
        layout.addWidget(btn_save)
        self.setLayout(layout)

    def _on_save_clicked(self):
        self.node_data = {
            "label": self.label_edit.text().strip(),
            "properties": self.editor.get_properties()
        }
        self.accept()


class RelationshipDialog(QDialog):
    def __init__(self, rel_type, rel_props=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Редактировать связь {rel_type}")
        self.setModal(True)
        layout = QVBoxLayout(self)

        self.editor = PropertyEditor(rel_props or {})
        layout.addWidget(self.editor)

        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._on_save_clicked)
        layout.addWidget(btn_save)
        self.setLayout(layout)

    def _on_save_clicked(self):
        self.rel_data = {"properties": self.editor.get_properties()}
        self.accept()


class NewNodeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать новый узел")
        self.setModal(True)
        layout = QVBoxLayout(self)

        self.label_edit = QLineEdit()
        self.label_edit.textChanged.connect(self.update_preview)
        layout.addWidget(QLabel("Метка узла:"))
        layout.addWidget(self.label_edit)

        self.editor = PropertyEditor()
        layout.addWidget(self.editor)

        layout.addWidget(QLabel("Предпросмотр узла:"))
        self.preview_view = QWebEngineView()
        layout.addWidget(self.preview_view)

        btn_save = QPushButton("Создать")
        btn_save.clicked.connect(self.accept)
        layout.addWidget(btn_save)
        self.setLayout(layout)

        self._last_preview = None
        self.update_preview()

    def get_data(self):
        return {"label": self.label_edit.text().strip(), "properties": self.editor.get_properties()}

    def update_preview(self):
        label = self.label_edit.text() or "Node"
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

    def closeEvent(self, event):
        if self._last_preview and os.path.exists(self._last_preview):
            try:
                os.remove(self._last_preview)
            except OSError:
                pass
        super().closeEvent(event)


class NewRelationshipDialog(QDialog):
    def __init__(self, nodes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать новое отношение")
        self.setModal(True)
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

        self.type_edit = QLineEdit("REL_TYPE")
        layout.addWidget(QLabel("Тип отношения:"))
        layout.addWidget(self.type_edit)

        self.direction_box = QComboBox()
        self.direction_box.addItems(["->", "<-", "двунаправленное"])
        layout.addWidget(QLabel("Направление:"))
        layout.addWidget(self.direction_box)

        self.editor = PropertyEditor()
        layout.addWidget(self.editor)

        btn_save = QPushButton("Создать")
        btn_save.clicked.connect(self.accept)
        layout.addWidget(btn_save)
        self.setLayout(layout)

    def get_data(self):
        from_id = self.from_box.currentData()
        to_id = self.to_box.currentData()
        r_type = self.type_edit.text().strip() or "REL"
        direction = self.direction_box.currentText()
        props = self.editor.get_properties()
        logger.debug("NewRelationshipDialog.get_data -> from=%s to=%s type=%s props=%s", from_id, to_id, r_type, props)
        return {
            "from": from_id,
            "to": to_id,
            "type": r_type,
            "direction": direction,
            "properties": props
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
            # Запрос графа синхронно не нужен - мы уже поддерживаем актуальность
            nodes, _ = main.client.get_graph()
            node = next((n for n in nodes if str(n.get("id")) == str(element_id)), None)
            if node:
                dlg = NodeDialog(node_id=node["id"], node_label=node["label"], node_props=node["properties"], parent=main)
                if dlg.exec_() == QDialog.Accepted:
                    data = dlg.node_data
                    # выполняем обновление в воркере
                    main.submit_task(lambda: main.client.update_node_properties(node["id"], data["properties"]), 'update_node')
        else:  # edge
            _, rels = main.client.get_graph()
            rel = next((r for r in rels if str(r.get("id")) == str(element_id)), None)
            if rel:
                dlg = RelationshipDialog(rel_type=rel["type"], rel_props=rel["properties"], parent=main)
                if dlg.exec_() == QDialog.Accepted:
                    data = dlg.rel_data
                    main.submit_task(lambda: main.client.update_relationship_properties(rel["id"], data["properties"]), 'update_rel')


# ---------------------------
# Главное окно
# ---------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Neo4j PyQt App (async)")
        # Установим разумный размер окна по умолчанию и минимальный размер
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)
        # Попробуем центрировать окно на основном экране (без падения при ошибках)
        try:
            screen_geom = QApplication.primaryScreen().availableGeometry()
            x = (screen_geom.width() - self.width()) // 2
            y = (screen_geom.height() - self.height()) // 2
            self.move(x, y)
        except Exception:
            pass
        self.client = Neo4jClient(password="testtest")

        self.view = QWebEngineView()
        layout = QVBoxLayout()
        layout.addWidget(self.view)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

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

        menubar = self.menuBar()
        file_menu = menubar.addMenu("Файл")
        export_action = QAction("Экспортировать граф", self)
        export_action.triggered.connect(self._export_graph)
        file_menu.addAction(export_action)

        # пул потоков для выполнения DB-операций
        self.pool = QThreadPool.globalInstance()

        self._populate_filters()
        # загружаем граф асинхронно
        self._load_graph_async()

    # ---------------------------
    # Вспомогательные методы для отправки задач в пул
    # ---------------------------
    def submit_task(self, fn, task_name=None, *args, **kwargs):
        worker = Worker(partial(fn, *args, **kwargs), task_name=task_name)
        worker.signals.result.connect(self._on_task_result)
        worker.signals.error.connect(self._on_task_error)
        worker.signals.finished.connect(lambda: None)
        self.pool.start(worker)
        return worker

    def _on_task_result(self, payload):
        task = payload.get('task')
        result = payload.get('result')
        logger.debug("Task finished: %s", task)
        if task == 'get_graph':
            # result is (nodes, rels)
            nodes, rels = result
            self._apply_graph_to_view(nodes, rels)
        elif task == 'get_types':
            types = result
            self._apply_filters(types)
        else:
            # Для CRUD операций после выполнения обновим граф
            if task in ('add_node', 'add_rel', 'update_node', 'update_rel'):
                # Обновим фильтры и перезагрузим граф
                self._populate_filters_async()
                self._load_graph_async()

    def _on_task_error(self, payload):
        task = payload.get('task')
        err = payload.get('error')
        logger.exception("Error in task %s: %s", task, err)
        QMessageBox.critical(self, f"Ошибка в задаче {task}", str(err))

    # ---------------------------
    # Фильтры
    # ---------------------------
    def _populate_filters(self):
        try:
            with self.client.driver.session() as session:
                result = session.run("MATCH (n) WHERE n.`тип` IS NOT NULL RETURN DISTINCT n.`тип` as t")
                types = [rec["t"] for rec in result if rec["t"]]
            types = ["Все"] + sorted(set(types))
        except Exception:
            types = ["Все"]
        self.filter_box.clear()
        self.filter_box.addItems(types)

    def _populate_filters_async(self):
        def task():
            with self.client.driver.session() as session:
                result = session.run("MATCH (n) WHERE n.`тип` IS NOT NULL RETURN DISTINCT n.`тип` as t")
                return [rec["t"] for rec in result if rec["t"]]
        self.submit_task(task, 'get_types')
        def task():
            with self.client.driver.session() as session:
                result = session.run(get_node_types_query())
                return [rec["t"] for rec in result if rec["t"]]
        self.submit_task(task, 'get_types')

    def _apply_filters(self, types):
        types = ["Все"] + sorted(set(types))
        self.filter_box.clear()
        self.filter_box.addItems(types)

    # ---------------------------
    # Загрузка графа (асинхронно)
    # ---------------------------
    def _load_graph_async(self):
        # отправляем задачу на получение графа
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
                arrows = "to" if r.get("direction", "->") == "->" else "from" if r.get("direction") == "<-" else "to,from"
                net.add_edge(r["from"], r["to"], label=r["type"], title=str(r.get("properties", {})), arrows=arrows, id=r["id"])

            file_path = os.path.abspath("graph.html")
            net.write_html(file_path, notebook=False)

            with open(file_path, "r", encoding="utf-8") as f:
                html = f.read()

            html = html.replace("</body>", _js_bridge_script())
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html)

            # загружаем в UI (в main thread)
            self.view.load(QUrl.fromLocalFile(file_path))
        except Exception as e:
            logger.exception("Error applying graph to view: %s", e)

    def _reload_graph(self, selected_type):
        # при смене фильтра просто повторно запрашиваем граф
        self._load_graph_async()

    # ---------------------------
    # Создание/редактирование сущностей
    # ---------------------------
    def _create_node(self):
        dlg = NewNodeDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            # запускаем создание в пуле
            self.submit_task(lambda: self.client.add_node(data["label"], data["properties"]), 'add_node')

    def _create_relationship(self):
        nodes, _ = self.client.get_graph()
        dlg = NewRelationshipDialog(nodes, self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            from_id = data["from"]
            to_id = data["to"]
            if data["direction"] == "<-":
                from_id, to_id = to_id, from_id
            self.submit_task(lambda: self.client.add_relationship(from_id, to_id, data["type"], data["direction"], data["properties"]), 'add_rel')

    # ---------------------------
    # Экспорт
    # ---------------------------
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
    w.show()
    sys.exit(app.exec_())
