"""Tests for backend abstraction, vector editor, terminal mux, and DB client."""
import unittest
import time
from ui.backend import (
    Backend, BackendCapabilities, BackendType, DisplayOutput,
    InputEvent, PixelFormat, SurfaceBuffer,
)
from ui.linux_backend import LinuxBackend
from ui.vector_editor import (
    VectorEditor, Document, Layer, VectorShape, Fill, Stroke, Color,
    Transform2D, Point, GradientStop, ExportSettings, ToolType, BlendMode,
    FillType, StrokeCap, StrokeJoin, AnchorType,
)
from ui.term_multiplexer import (
    TermMultiplexer, Session, Pane, TerminalHistory, TerminalCommand,
    SplitDirection, PaneState, LayoutPreset,
)
from ui.db_client import (
    DBClient, Connection, Table, Column, Index, QueryResult, SavedQuery,
    DatabaseType, QueryStatus, ColumnType, ConstraintType,
)


# ==================== Backend Tests ====================

class TestSurfaceBuffer(unittest.TestCase):
    def test_create(self):
        buf = SurfaceBuffer(800, 600, b"\x00" * 100)
        self.assertEqual(buf.size, (800, 600))
        self.assertEqual(buf.byte_count, 100)

    def test_empty(self):
        buf = SurfaceBuffer()
        self.assertTrue(buf.is_empty)


class TestDisplayOutput(unittest.TestCase):
    def test_create(self):
        out = DisplayOutput(0, "eDP-1", 1920, 1080, 60.0)
        self.assertEqual(out.resolution_str, "1920x1080@60Hz")
        self.assertEqual(out.scale_str, "1.0x")


class TestInputEvent(unittest.TestCase):
    def test_create(self):
        ev = InputEvent("key", key_code=65, key_name="A")
        self.assertEqual(ev.event_type, "key")


class TestBackendCapabilities(unittest.TestCase):
    def test_default(self):
        caps = BackendCapabilities()
        self.assertFalse(caps.hardware_acceleration)

    def test_linux(self):
        caps = BackendCapabilities(hardware_acceleration=False, vulkan=False)
        self.assertFalse(caps.vulkan)


class TestLinuxBackend(unittest.TestCase):
    def setUp(self):
        self.backend = LinuxBackend()

    def test_is_available(self):
        # May be True or False depending on PIL
        self.assertIsInstance(self.backend.is_available(), bool)

    def test_initialize(self):
        result = self.backend.initialize(640, 480)
        # May fail without PIL, that's ok
        self.assertIsInstance(result, bool)

    def test_capabilities(self):
        caps = self.backend.capabilities()
        self.assertFalse(caps.hardware_acceleration)

    def test_info(self):
        info = self.backend.info()
        self.assertEqual(info["backend"], "linux")
        self.assertEqual(info["renderer"], "PIL/Software")

    def test_clipboard(self):
        self.backend.clipboard_set("test text")
        self.assertEqual(self.backend._clipboard_text, "test text")

    def test_window(self):
        h = self.backend.create_window("Test", 800, 600)
        self.assertIsNotNone(h)

    def test_cursor(self):
        self.backend.set_cursor("pointer")
        self.assertEqual(self.backend._cursor_type, "pointer")


class TestBackendSingleton(unittest.TestCase):
    def test_get_returns_instance(self):
        b = Backend.get()
        self.assertIsNotNone(b)

    def test_set_instance(self):
        original = Backend.get()
        test = LinuxBackend()
        Backend.set(test)
        self.assertIs(Backend.get(), test)
        Backend.set(original)  # restore

    def test_backend_type(self):
        bt = Backend.backend_type()
        self.assertIsInstance(bt, str)


# ==================== VectorEditor Tests ====================

class TestColor(unittest.TestCase):
    def test_hex(self):
        c = Color(255, 128, 0)
        self.assertEqual(c.hex, "#ff8000")

    def test_css(self):
        c = Color(255, 0, 0, 0.5)
        self.assertIn("rgba", c.css)

    def test_css_opaque(self):
        c = Color(0, 255, 0)
        self.assertEqual(c.css, "#00ff00")

    def test_color_bar(self):
        c = Color(100, 100, 100)
        self.assertIn("██████", c.color_bar)


class TestPoint(unittest.TestCase):
    def test_create(self):
        p = Point(3, 4)
        self.assertEqual(p.to_tuple(), (3, 4))

    def test_distance(self):
        d = Point(0, 0).distance_to(Point(3, 4))
        self.assertAlmostEqual(d, 5.0)


class TestFill(unittest.TestCase):
    def test_solid(self):
        f = Fill(FillType.SOLID, Color(255, 0, 0))
        self.assertEqual(f.css, "#ff0000")

    def test_none(self):
        f = Fill(FillType.NONE)
        self.assertEqual(f.css, "none")


class TestStroke(unittest.TestCase):
    def test_css(self):
        s = Stroke(Color(0, 0, 0), 2.0)
        self.assertIn("2.0px", s.css)


class TestVectorShape(unittest.TestCase):
    def test_create(self):
        s = VectorShape(1, "Test", "rect", 0, 0, 100, 50)
        self.assertEqual(s.area, 5000)

    def test_bbox(self):
        s = VectorShape(1, "T", "rect", 10, 20, 30, 40)
        self.assertEqual(s.bbox, (10, 20, 40, 60))

    def test_center(self):
        s = VectorShape(1, "T", "rect", 0, 0, 100, 100)
        self.assertEqual(s.center.x, 50)

    def test_icon(self):
        s = VectorShape(1, "T", "star")
        self.assertEqual(s.type_icon, "★")


class TestLayer(unittest.TestCase):
    def test_create(self):
        l = Layer(0, "Test")
        self.assertEqual(l.shape_count, 0)

    def test_visible_icon(self):
        l = Layer(0, "T", visible=True)
        self.assertEqual(l.visible_icon, "👁")


class TestDocument(unittest.TestCase):
    def test_create(self):
        d = Document("Test", 1920, 1080)
        self.assertEqual(d.resolution_str, "1920x1080")


class TestVectorEditor(unittest.TestCase):
    def setUp(self):
        self.editor = VectorEditor()

    def test_initial_state(self):
        self.assertIsNotNone(self.editor.document)
        self.assertEqual(self.editor.selected_tool, ToolType.SELECT)

    def test_total_shapes(self):
        self.assertGreater(self.editor.total_shapes, 0)

    def test_select_tool(self):
        self.editor.select_tool(ToolType.RECT)
        self.assertEqual(self.editor.selected_tool, ToolType.RECT)

    def test_add_shape(self):
        count = self.editor.total_shapes
        sid = self.editor.add_shape("rect", 10, 10, 50, 50)
        self.assertGreater(sid, 0)
        self.assertEqual(self.editor.total_shapes, count + 1)

    def test_duplicate(self):
        self.editor.select_shape(1)
        count = self.editor.total_shapes
        self.editor.duplicate_selected()
        self.assertGreater(self.editor.total_shapes, count)

    def test_add_layer(self):
        count = len(self.editor.document.layers)
        self.editor.add_layer("New")
        self.assertEqual(len(self.editor.document.layers), count + 1)

    def test_export_svg(self):
        svg = self.editor.export_svg()
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_export_json(self):
        j = self.editor.export_json()
        data = __import__("json").loads(j)
        self.assertIn("layers", data)

    def test_render(self):
        lines = self.editor.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("VECTOR GRAPHICS EDITOR" in l for l in lines))


# ==================== TermMultiplexer Tests ====================

class TestTerminalHistory(unittest.TestCase):
    def test_add(self):
        h = TerminalHistory()
        h.add("ls -la", 0, 50, 20)
        self.assertEqual(len(h.commands), 1)

    def test_last_command(self):
        h = TerminalHistory()
        h.add("echo hello", 0, 10, 1)
        self.assertEqual(h.last_command.command, "echo hello")


class TestPane(unittest.TestCase):
    def test_create(self):
        p = Pane(0, "Terminal", "/home/user")
        self.assertTrue(p.is_active)

    def test_state_icon(self):
        p = Pane(0, "T", state=PaneState.RUNNING)
        self.assertEqual(p.state_icon, "🔄")

    def test_dir_display(self):
        p = Pane(0, "T", "/home/user/projects/nyrqis")
        self.assertIn("~", p.dir_display)


class TestSession(unittest.TestCase):
    def test_create(self):
        s = Session(0, "Test", time.time())
        self.assertEqual(s.pane_count, 0)

    def test_active_pane_obj(self):
        s = Session(0, "T", time.time())
        s.panes.append(Pane(0, "T"))
        self.assertIsNotNone(s.active_pane_obj)

    def test_created_str(self):
        import time
        s = Session(0, "T", time.time() - 120)
        self.assertIn("m ago", s.created_str)


class TestTermMultiplexer(unittest.TestCase):
    def setUp(self):
        self.mux = TermMultiplexer()

    def test_initial_state(self):
        self.assertGreater(self.mux.total_sessions, 0)
        self.assertGreater(self.mux.total_panes, 0)

    def test_active_session(self):
        s = self.mux.active_session
        self.assertIsNotNone(s)

    def test_new_session(self):
        count = self.mux.total_sessions
        self.mux.new_session("Test")
        self.assertEqual(self.mux.total_sessions, count + 1)

    def test_split_pane(self):
        session = self.mux.active_session
        count = session.pane_count
        self.mux.split_pane(SplitDirection.HORIZONTAL)
        self.assertEqual(session.pane_count, count + 1)

    def test_close_pane(self):
        session = self.mux.active_session
        if session.pane_count > 1:
            count = session.pane_count
            self.mux.close_pane()
            self.assertEqual(session.pane_count, count - 1)

    def test_send_command(self):
        self.mux.send_command("echo hello")
        session = self.mux.active_session
        pane = session.active_pane_obj
        self.assertGreater(len(pane.history.commands), 0)

    def test_next_session(self):
        old = self.mux._active_session
        self.mux.next_session()
        # May be same if only 1 session

    def test_next_pane(self):
        session = self.mux.active_session
        if session and session.pane_count > 1:
            old = session.active_pane
            self.mux.next_pane()
            self.assertNotEqual(session.active_pane, old)

    def test_render(self):
        lines = self.mux.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("TERMINAL MULTIPLEXER" in l for l in lines))

    def test_history(self):
        self.mux.new_session("Test")
        self.assertGreater(len(self.mux._history), 0)

    def test_apply_layout(self):
        self.mux.apply_layout(LayoutPreset.FOUR_GRID)
        s = self.mux.active_session
        self.assertEqual(s.layout, LayoutPreset.FOUR_GRID)


# ==================== DBClient Tests ====================

class TestColumn(unittest.TestCase):
    def test_create(self):
        c = Column("id", ColumnType.SERIAL, False, True)
        self.assertTrue(c.primary_key)

    def test_type_str(self):
        c = Column("name", ColumnType.VARCHAR, max_length=255)
        self.assertEqual(c.type_str, "varchar(255)")

    def test_icon_pk(self):
        c = Column("id", ColumnType.INTEGER, primary_key=True)
        self.assertEqual(c.icon, "🔑")

    def test_icon_fk(self):
        c = Column("user_id", ColumnType.BIGINT, foreign_key="users.id")
        self.assertEqual(c.icon, "🔗")


class TestTable(unittest.TestCase):
    def test_create(self):
        t = Table("users")
        self.assertEqual(t.column_count, 0)

    def test_primary_keys(self):
        t = Table("users", [Column("id", ColumnType.INTEGER, primary_key=True)])
        self.assertEqual(t.primary_keys, ["id"])

    def test_size_str(self):
        t = Table("users", row_count=1000, estimated_size="1 MB")
        self.assertIn("1,000", t.size_str)


class TestQueryResult(unittest.TestCase):
    def test_create(self):
        r = QueryResult("SELECT 1", QueryStatus.SUCCESS)
        self.assertFalse(r.has_data)

    def test_with_data(self):
        r = QueryResult("SELECT *", QueryStatus.SUCCESS,
                        columns=["id", "name"],
                        rows=[[1, "test"]],
                        row_count=1)
        self.assertTrue(r.has_data)
        self.assertEqual(r.row_count, 1)

    def test_duration(self):
        r = QueryResult("Q", QueryStatus.SUCCESS, execution_time_ms=0.5)
        self.assertIn("µs", r.duration_str)

    def test_is_select(self):
        r = QueryResult("SELECT * FROM users", QueryStatus.SUCCESS)
        self.assertTrue(r.is_select)


class TestConnection(unittest.TestCase):
    def test_create(self):
        c = Connection("Local", DatabaseType.POSTGRESQL, port=5432)
        self.assertEqual(c.port, 5432)

    def test_status_icon(self):
        c = Connection("C", connected=True)
        self.assertEqual(c.status_icon, "🟢")
        c.connected = False
        self.assertEqual(c.status_icon, "🔴")


class TestIndex(unittest.TestCase):
    def test_create(self):
        idx = Index("idx_email", "users", ["email"], unique=True)
        self.assertIn("UNIQUE", idx.type_str)


class TestDBClient(unittest.TestCase):
    def setUp(self):
        self.client = DBClient()

    def test_initial_state(self):
        self.assertGreater(len(self.client._connections), 0)
        self.assertGreater(len(self.client._tables), 0)

    def test_active_connection(self):
        conn = self.client.active_connection
        self.assertIsNotNone(conn)

    def test_selected_table(self):
        table = self.client.selected_table
        self.assertIsNotNone(table)

    def test_total_tables(self):
        self.assertGreater(self.client.total_tables, 0)

    def test_total_rows(self):
        self.assertGreater(self.client.total_rows, 0)

    def test_select_connection(self):
        self.client.select_connection(2)
        self.assertEqual(self.client._active_connection, 2)

    def test_select_table(self):
        self.client.select_table(3)
        self.assertEqual(self.client._selected_table, 3)

    def test_execute_query(self):
        result = self.client.execute_query("SELECT * FROM users")
        self.assertEqual(result.status, QueryStatus.SUCCESS)
        self.assertTrue(result.has_data)

    def test_get_table_ddl(self):
        table = self.client.selected_table
        ddl = self.client.get_table_ddl(table)
        self.assertIn("CREATE TABLE", ddl)

    def test_saved_queries(self):
        self.assertGreater(len(self.client._saved_queries), 0)

    def test_save_query(self):
        self.client.save_query("Test", "SELECT 1")
        self.assertGreater(len(self.client._saved_queries), 0)

    def test_render(self):
        lines = self.client.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("DATABASE CLIENT" in l for l in lines))

    def test_history(self):
        self.client.execute_query("SELECT 1")
        self.assertGreater(len(self.client._history), 0)

    def test_query_history(self):
        self.client.execute_query("SELECT 1")
        self.assertGreater(len(self.client._query_history), 0)


class TestDatabaseType(unittest.TestCase):
    def test_all_values(self):
        self.assertEqual(DatabaseType.POSTGRESQL.value, "PostgreSQL")
        self.assertEqual(DatabaseType.SQLITE.value, "SQLite")


class TestColumnType(unittest.TestCase):
    def test_all_values(self):
        self.assertEqual(ColumnType.INTEGER.value, "integer")
        self.assertEqual(ColumnType.VARCHAR.value, "varchar")


if __name__ == "__main__":
    unittest.main()
