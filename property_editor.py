from PyQt5.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QPushButton, QLineEdit, QHBoxLayout


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
