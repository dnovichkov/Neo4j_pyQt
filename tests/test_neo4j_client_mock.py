import pytest
from unittest.mock import MagicMock
from neo_4j_client import Neo4jClient


@pytest.fixture
def mock_driver(monkeypatch):
    """Мокаем GraphDatabase.driver чтобы не подключаться к Neo4j"""
    mock_session = MagicMock()
    mock_session.run.return_value = []

    mock_driver_instance = MagicMock()
    mock_driver_instance.session.return_value.__enter__.return_value = mock_session

    monkeypatch.setattr("neo4j.GraphDatabase.driver", lambda uri, auth: mock_driver_instance)
    return mock_driver_instance


def test_add_node(mock_driver):
    client = Neo4jClient()
    result = client.add_node("Person", {"name": "Alice"})

    # Проверяем, что session.run вызван один раз
    mock_driver.session.return_value.__enter__.return_value.run.assert_called_once()

    # Проверяем, что результат – список (даже если мок пустой)
    assert isinstance(result, list)


def test_add_relationship(mock_driver):
    client = Neo4jClient()
    result = client.add_relationship("uuid1", "uuid2", "KNOWS", "->", {"since": 2023})

    mock_driver.session.return_value.__enter__.return_value.run.assert_called_once()
    assert isinstance(result, list)


def test_update_node_properties(mock_driver):
    client = Neo4jClient()
    client.update_node_properties("uuid1", {"age": 30})

    mock_driver.session.return_value.__enter__.return_value.run.assert_called_once()


def test_update_relationship_properties(mock_driver):
    client = Neo4jClient()
    client.update_relationship_properties("rel_uuid1", {"weight": 5})

    mock_driver.session.return_value.__enter__.return_value.run.assert_called_once()


def test_get_graph_empty(mock_driver):
    client = Neo4jClient()
    nodes, rels = client.get_graph()

    # Мок возвращает пустые результаты
    assert nodes == []
    assert rels == []
