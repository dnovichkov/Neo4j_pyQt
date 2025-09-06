import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QComboBox, QLabel, QVBoxLayout,
    QWidget, QAction, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QLineEdit, QPushButton
)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtCore import QUrl, pyqtSlot, QObject
from pyvis.network import Network
from neo4j import GraphDatabase


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
                node = record["n"]
                nodes.append({
                    "id": node.element_id,
                    "label": node.get("label", node.element_id),
                    "properties": dict(node.items())
                })

            rels_result = session.run("MATCH (a)-[r]->(b) RETURN r, a, b")
            rels = []
            for record in rels_result:
                r = record["r"]
                a = record["a"]
                b = record["b"]
                rels.append({
                    "from": a.element_id,
                    "to": b.element_id,
                    "type": r.type,
                    "properties": dict(r.items()),
                    "direction": "->"
                })
        return nodes, rels

    def add_node(self, label, properties):
        with self.driver.session() as session:
            props_str = ", ".join([f"{k}: ${k}" for k in properties])
            query = f"CREATE (n:{label} {{{props_str}}}) RETURN n"
            session.run(query, **properties)

    def add_relationship(self, from_id, to_id, r_type, direction, properties):
        with self.driver.session() as session:
            query = (
                "MATCH (a),(b) "
                "WHERE elementId(a) = $from_id AND elementId(b) = $to_id "
                f"CREATE (a)-[r:{r_type} $props]->(b) RETURN r"
            )
            print("DEBUG: Creating relationship")
            print("from_id:", from_id)
            print("to_id:", to_id)
            print("r_type:", r_type)
            print("properties:", properties)
            result = session.run(query, from_id=from_id, to_id=to_id, props=properties)
            created = list(result)
            print("DEBUG: Relationship created:", created)
            return created

    def update_node_properties(self, node_id, properties):
        with self.driver.session() as session:
            query = "MATCH (n) WHERE elementId(n)=$nid SET n += $props RETURN n"
            session.run(query, nid=node_id, props=properties)

    def update_relationship_properties(self, from_id, to_id, r_type, properties):
        with self.driver.session() as session:
            query = (
                f"MATCH (a)-[r:{r_type}]->(b) "
                "WHERE elementId(a)=$from_id AND elementId(b)=$to_id "
                "SET r += $props RETURN r"
            )
            session.run(query, from_id=from_id, to_id=to_id, props=properties)


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
        self.fields = []

        if properties:
            for k, v in properties.items():
                self.add_field(k, v)

        add_btn = QPushButton("Добавить поле")
        add_btn.clicked.connect(lambda: self.add_field())
        self.layout.addWidget(add_btn)

    def add_field(self, key="", value=""):
        key_edit = QLineEdit(str(key))
        val_edit = QLineEdit(str(value))
        self.form_layout.addRow(key_edit, val_edit)
        self.fields.append((key_edit, val_edit))

    def get_properties(self):
        return {k.text(): v.text() for k, v in self.fields if k.text()}


# ---------------------------
# Диалоги узлов и связей
# ---------------------------
class NodeDialog(QDialog):
    def __init__(self, node_id, node_label=None, node_props=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Узел {node_id}")
        layout = QVBoxLayout(self)

        props = node_props or {"тип": "Person"}
        self.editor = PropertyEditor(props)
        layout.addWidget(self.editor)

        self.label_edit = QLineEdit(node_label or f"Node {node_id}")
        layout.addWidget(QLabel("Метка узла:"))
        layout.addWidget(self.label_edit)

        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save)
        layout.addWidget(btn_save)
        self.setLayout(layout)

    def _save(self):
        self.node_data = {"label": self.label_edit.text(), "properties": self.editor.get_properties()}
        self.accept()


class RelationshipDialog(QDialog):
    def __init__(self, rel_type, rel_props=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Редактировать связь {rel_type}")
        layout = QVBoxLayout(self)

        self.editor = PropertyEditor(rel_props or {})
        layout.addWidget(self.editor)

        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save)
        layout.addWidget(btn_save)
        self.setLayout(layout)

    def _save(self):
        self.rel_data = {"properties": self.editor.get_properties()}
        self.accept()


# ---------------------------
# Диалоги создания нового узла/отношения
# ---------------------------
class NewNodeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать новый узел")
        layout = QVBoxLayout(self)

        self.label_edit = QLineEdit()
        layout.addWidget(QLabel("Метка узла:"))
        layout.addWidget(self.label_edit)

        self.editor = PropertyEditor()
        layout.addWidget(self.editor)

        btn_save = QPushButton("Создать")
        btn_save.clicked.connect(self.accept)
        layout.addWidget(btn_save)
        self.setLayout(layout)

    def get_data(self):
        return {"label": self.label_edit.text(), "properties": self.editor.get_properties()}


class NewRelationshipDialog(QDialog):
    def __init__(self, nodes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать новое отношение")
        layout = QVBoxLayout(self)

        self.from_box = QComboBox()
        self.to_box = QComboBox()
        for n in nodes:
            self.from_box.addItem(n["label"], n["id"])
            self.to_box.addItem(n["label"], n["id"])

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
        r_type = self.type_edit.text().strip()
        direction = self.direction_box.currentText()
        props = self.editor.get_properties()
        print(f"DEBUG: get_data() -> from_id={from_id}, to_id={to_id}, type={r_type}, props={props}")
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
            nodes, _ = main.client.get_graph()
            node = next((n for n in nodes if n["id"] == element_id), None)
            if node:
                dlg = NodeDialog(node_id=element_id, node_label=node["label"], node_props=node["properties"], parent=main)
                if dlg.exec_() == QDialog.Accepted:
                    data = dlg.node_data
                    main.client.update_node_properties(element_id, data["properties"])
                    main._load_graph(main.filter_box.currentText())
        else:
            _, rels = main.client.get_graph()
            rel = next((r for r in rels if r["from"] == element_id or r["to"] == element_id), None)
            if rel:
                dlg = RelationshipDialog(rel_type=rel["type"], rel_props=rel["properties"], parent=main)
                if dlg.exec_() == QDialog.Accepted:
                    data = dlg.rel_data
                    main.client.update_relationship_properties(rel["from"], rel["to"], rel["type"], data["properties"])
                    main._load_graph(main.filter_box.currentText())


# ---------------------------
# Главное окно
# ---------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Neo4j PyQt App")

        # сначала создаём клиент
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
        self.filter_box.addItems(["Все", "Person", "Company", "Project"])
        self.filter_box.currentTextChanged.connect(self._reload_graph)
        toolbar.addWidget(self.filter_box)

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

        # загрузка графа
        self._load_graph()

    def _reload_graph(self, selected_type):
        self._load_graph(selected_type)

    def _load_graph(self, selected_type="Все"):
        nodes, rels = self.client.get_graph()
        if selected_type != "Все":
            nodes = [n for n in nodes if n["properties"].get("тип") == selected_type]
            node_ids = {n["id"] for n in nodes}
            rels = [r for r in rels if r["from"] in node_ids and r["to"] in node_ids]

        print(f"DEBUG: Loaded {len(nodes)} nodes and {len(rels)} edges from Neo4j")

        net = Network(height="750px", width="100%", directed=True)
        for n in nodes:
            net.add_node(
                n["id"],
                label=n.get("label", str(n["id"])),
                title=str(n.get("properties", {}))
            )
        for r in rels:
            arrows = "to" if r.get("direction", "->") == "->" else "from" if r.get("direction") == "<-" else "to,from"
            net.add_edge(r["from"], r["to"], label=r["type"], title=str(r.get("properties", {})), arrows=arrows)

        file_path = os.path.abspath("graph.html")
        net.write_html(file_path, notebook=False)

        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()

        inject = """
        <script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <script>
        new QWebChannel(qt.webChannelTransport, function(channel) {
            window.bridge = channel.objects.bridge;
            network.on("click", function(params) {
                if(params.nodes.length > 0){
                    bridge.onNodeClicked("node", params.nodes[0].toString());
                } else if(params.edges.length > 0){
                    bridge.onNodeClicked("edge", params.edges[0].toString());
                }
            });
        });
        </script>
        </body>
        """
        html = html.replace("</body>", inject)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html)

        self.view.load(QUrl.fromLocalFile(file_path))

    def _create_node(self):
        dlg = NewNodeDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            self.client.add_node(data["label"], data["properties"])
            self._load_graph(self.filter_box.currentText())

    def _create_relationship(self):
        nodes, _ = self.client.get_graph()
        dlg = NewRelationshipDialog(nodes, self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            self.client.add_relationship(data["from"], data["to"], data["type"], data["direction"], data["properties"])
            self._load_graph(self.filter_box.currentText())

    def _export_graph(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить граф как HTML", "", "HTML Files (*.html)")
        if path:
            try:
                nodes, rels = self.client.get_graph()
                net = Network(height="750px", width="100%", directed=True)
                for n in nodes:
                    net.add_node(n["id"], label=n.get("label", str(n["id"])), title=str(n.get("properties", {})))
                for r in rels:
                    net.add_edge(r["from"], r["to"], label=r["type"], title=str(r.get("properties", {})))
                net.write_html(path, notebook=False)
            except Exception as e:
                QMessageBox.critical(self, "Ошибка экспорта", str(e))


# ---------------------------
# Точка входа
# ---------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
