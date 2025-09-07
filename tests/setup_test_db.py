"""
Скрипт для подготовки тестовой Neo4j БД.
Создаёт узлы и связи для unit/integration тестов.
Можно запускать локально или в CI.
"""

from neo4j import GraphDatabase

# Настройки тестовой БД
URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "testtest"

# Данные для создания
NODES = [
    {"uuid": "1", "label": "Person", "name": "Alice", "тип": "User"},
    {"uuid": "2", "label": "Person", "name": "Bob", "тип": "User"},
    {"uuid": "3", "label": "Company", "name": "Neo4jInc", "тип": "Company"},
]

RELATIONSHIPS = [
    {"uuid": "r1", "from": "1", "to": "3", "type": "WORKS_AT"},
    {"uuid": "r2", "from": "2", "to": "3", "type": "WORKS_AT"},
    {"uuid": "r3", "from": "1", "to": "2", "type": "KNOWS"},
]

def setup_database():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    with driver.session() as session:
        # Очистка всех данных
        session.run("MATCH (n) DETACH DELETE n")
        print("База очищена")

        # Создание узлов
        for node in NODES:
            query = f"CREATE (n:{node['label']}) SET n = $props RETURN n"
            props = {k: v for k, v in node.items() if k != "label"}
            session.run(query, props=props)
        print(f"Создано {len(NODES)} узлов")

        # Создание связей
        for rel in RELATIONSHIPS:
            query = (
                "MATCH (a {uuid:$from_uuid}), (b {uuid:$to_uuid}) "
                f"CREATE (a)-[r:{rel['type']} {{uuid:$uuid}}]->(b) RETURN r"
            )
            session.run(query, from_uuid=rel["from"], to_uuid=rel["to"], uuid=rel["uuid"])
        print(f"Создано {len(RELATIONSHIPS)} связей")

    driver.close()
    print("Подключение закрыто")

if __name__ == "__main__":
    setup_database()
