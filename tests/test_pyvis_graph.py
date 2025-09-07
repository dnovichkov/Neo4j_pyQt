import pytest
from pyvis.network import Network


def test_create_pyvis_graph(tmp_path):
    net = Network(height="400px", width="600px", directed=True)
    net.add_node(1, label="Alice")
    net.add_node(2, label="Bob")
    net.add_edge(1, 2, label="KNOWS")

    output_file = tmp_path / "test_graph.html"
    net.write_html(str(output_file))

    assert output_file.exists()
    assert output_file.read_text().strip() != ""
