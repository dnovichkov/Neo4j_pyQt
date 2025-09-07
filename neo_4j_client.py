import uuid

from neo4j import GraphDatabase

from log_utils import logger


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
