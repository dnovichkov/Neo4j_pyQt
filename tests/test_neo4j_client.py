import pytest
from neo_4j_client import Neo4jClient
from tests.setup_test_db import setup_database

@pytest.fixture(scope="function")
def neo4j_client():
    """
    Фикстура для Neo4jClient.
    Перед каждым тестом очищает и заполняет тестовую БД.
    """
    setup_database()
    client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="testtest")
    yield client
    client.close()


def test_get_graph(neo4j_client):
    nodes, rels = neo4j_client.get_graph()
    assert len(nodes) == 3, "Должно быть 3 узла"
    assert len(rels) == 3, "Должно быть 3 связи"

    # Проверяем конкретные данные
    node_labels = {n['label'] for n in nodes}
    assert "Person" in node_labels
    assert "Company" in node_labels

    rel_types = {r['type'] for r in rels}
    assert "WORKS_AT" in rel_types
    assert "KNOWS" in rel_types


def test_add_node(neo4j_client):
    result = neo4j_client.add_node("Person", {"name": "Charlie"})
    nodes, _ = neo4j_client.get_graph()
    assert any(n['properties'].get("name") == "Charlie" for n in nodes)


def test_add_relationship(neo4j_client):
    # Берём два существующих узла
    nodes, _ = neo4j_client.get_graph()
    from_uuid = nodes[0]['id']
    to_uuid = nodes[1]['id']

    neo4j_client.add_relationship(from_uuid, to_uuid, "FRIEND", "->", {"since": 2025})
    _, rels = neo4j_client.get_graph()
    assert any(r['type'] == "FRIEND" and r['properties'].get("since") == 2025 for r in rels)


def test_update_node_properties(neo4j_client):
    nodes, _ = neo4j_client.get_graph()
    node_uuid = nodes[0]['id']

    neo4j_client.update_node_properties(node_uuid, {"age": 30})
    nodes, _ = neo4j_client.get_graph()
    updated_node = next(n for n in nodes if n['id'] == node_uuid)
    assert updated_node['properties'].get("age") == 30


def test_update_relationship_properties(neo4j_client):
    _, rels = neo4j_client.get_graph()
    rel_uuid = rels[0]['id']

    neo4j_client.update_relationship_properties(rel_uuid, {"weight": 1.5})
    _, rels = neo4j_client.get_graph()
    updated_rel = next(r for r in rels if r['id'] == rel_uuid)
    assert updated_rel['properties'].get("weight") == 1.5
