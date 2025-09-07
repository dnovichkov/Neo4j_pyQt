import os
import tempfile

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QHBoxLayout, QPushButton, QComboBox, QFormLayout
from pyvis.network import Network

from log_utils import logger
from property_editor import PropertyEditor


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
