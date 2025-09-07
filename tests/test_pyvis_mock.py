import pytest
from unittest.mock import MagicMock, patch
from pyvis.network import Network
from neo_4j_client import Neo4jClient

@pytest.fixture
def mock_neo4j_data():
    """Возвращаем мок-данные, как будто их отдала Neo4j"""
    nodes = [
        {"id": "1", "label": "Alice", "properties": {"name": "Alice"}},
        {"id": "2", "label": "Bob", "properties": {"name": "Bob"}}
    ]
    rels = [
        {"id": "r1", "from": "1", "to": "2", "type": "KNOWS", "properties": {"since": 2023}, "direction": "->"}
    ]
    return nodes, rels

def test_pyvis_graph_creation(mock_neo4j_data):
    nodes, rels = mock_neo4j_data

    # Мокаем PyVis.Network.write_html чтобы не создавать файл
    with patch("pyvis.network.Network.write_html") as mock_write_html:
        net = Network(height="400px", width="600px", directed=True)

        # Добавляем мок-данные
        for node in nodes:
            net.add_node(node["id"], label=node["label"])
        for rel in rels:
            net.add_edge(rel["from"], rel["to"], label=rel["type"])

        # Пытаемся "сохранить" граф (замокали метод)
        net.write_html("dummy_path.html")
        mock_write_html.assert_called_once_with("dummy_path.html")

        # Проверяем, что узлы и ребра добавлены
        assert len(net.nodes) == 2
        assert len(net.edges) == 1

def test_pyvis_graph_from_neo4j_mock():
    """Полная интеграция с мок-объектом Neo4jClient"""
    mock_client = MagicMock(spec=Neo4jClient)
    mock_client.get_graph.return_value = (
        [
            {"id": "1", "label": "Alice", "properties": {"name": "Alice"}},
            {"id": "2", "label": "Bob", "properties": {"name": "Bob"}}
        ],
        [
            {"id": "r1", "from": "1", "to": "2", "type": "KNOWS", "properties": {"since": 2023}, "direction": "->"}
        ]
    )

    with patch("pyvis.network.Network.write_html") as mock_write_html:
        nodes, rels = mock_client.get_graph()
        net = Network(height="400px", width="600px", directed=True)
        for node in nodes:
            net.add_node(node["id"], label=node["label"])
        for rel in rels:
            net.add_edge(rel["from"], rel["to"], label=rel["type"])

        net.write_html("dummy.html")
        mock_write_html.assert_called_once_with("dummy.html")
        assert len(net.nodes) == 2
        assert len(net.edges) == 1
