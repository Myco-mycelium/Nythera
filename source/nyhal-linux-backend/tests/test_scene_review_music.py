"""Tests for Scene Editor, Code Review, and Music Server."""
import unittest
import time
from ui.scene_editor import (
    SceneEditor, SceneObject, SceneLight, SceneCamera, Material, Vec3,
    Animation, Keyframe, ViewportSettings,
    ObjectType, LightType, MaterialMode, TransformMode, SnapTarget,
)
from ui.code_review import (
    CodeReview, PullRequest, Reviewer, FileDiff, DiffLine, ReviewComment,
    InlineSuggestion,
    ReviewStatus, CommentType, Severity, DiffType,
)
from ui.music_server import (
    MusicServer, Track, Playlist, QueueItem, Lyrics, LyricLine, AudioEQBand,
    RepeatMode, AudioQuality, Genre,
)


# ==================== SceneEditor Tests ====================

class TestVec3(unittest.TestCase):
    def test_add(self):
        self.assertEqual((Vec3(1,2,3) + Vec3(4,5,6)).x, 5)

    def test_sub(self):
        self.assertEqual((Vec3(5,5,5) - Vec3(1,1,1)).x, 4)

    def test_mul(self):
        self.assertEqual((Vec3(2,3,4) * 2).x, 4)

    def test_length(self):
        self.assertAlmostEqual(Vec3(3,4,0).length(), 5.0)


class TestMaterial(unittest.TestCase):
    def test_hex(self):
        m = Material(base_color=(255, 128, 0))
        self.assertEqual(m.color_hex, "#ff8000")

    def test_bars(self):
        m = Material(metallic=0.5, roughness=0.8)
        self.assertIn("█", m.metallic_bar)
        self.assertIn("█", m.roughness_bar)


class TestSceneObject(unittest.TestCase):
    def test_type_icon(self):
        o = SceneObject(0, "T", ObjectType.SPHERE)
        self.assertEqual(o.type_icon, "🔵")

    def test_position_str(self):
        o = SceneObject(0, "T", position=Vec3(1.5, 2.5, 3.5))
        self.assertIn("1.50", o.position_str)


class TestSceneLight(unittest.TestCase):
    def test_intensity_bar(self):
        l = SceneLight(0, intensity=1.5)
        bar = l.intensity_bar
        self.assertIn("█", bar)


class TestAnimation(unittest.TestCase):
    def test_duration(self):
        a = Animation(start_frame=1, end_frame=301, fps=30)
        self.assertAlmostEqual(a.duration_s, 10.0)

    def test_frame_bar(self):
        a = Animation(start_frame=1, end_frame=101, current_frame=50)
        bar = a.frame_bar
        self.assertIn("▼", bar)


class TestSceneEditor(unittest.TestCase):
    def setUp(self):
        self.ed = SceneEditor()

    def test_initial_state(self):
        self.assertGreater(self.ed.total_objects, 0)

    def test_selected_object(self):
        o = self.ed.selected_object
        self.assertIsNotNone(o)

    def test_select_object(self):
        self.ed.select_object(3)
        self.assertEqual(self.ed._selected_object, 3)

    def test_total_vertices(self):
        self.assertGreater(self.ed.total_vertices, 0)

    def test_add_object(self):
        count = self.ed.total_objects
        self.ed.add_object(ObjectType.CUBE, Vec3(5, 0, 0))
        self.assertEqual(self.ed.total_objects, count + 1)

    def test_duplicate(self):
        self.ed.select_object(0)
        count = self.ed.total_objects
        self.ed.duplicate_selected()
        self.assertEqual(self.ed.total_objects, count + 1)

    def test_lights(self):
        self.assertGreater(len(self.ed._lights), 0)

    def test_cameras(self):
        self.assertGreater(len(self.ed._cameras), 0)

    def test_render(self):
        lines = self.ed.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("3D SCENE EDITOR" in l for l in lines))


# ==================== CodeReview Tests ====================

class TestDiffLine(unittest.TestCase):
    def test_prefix(self):
        dl = DiffLine(1, "test", DiffType.ADDED)
        self.assertEqual(dl.prefix, "+")

    def test_color_hint(self):
        dl = DiffLine(1, "test", DiffType.REMOVED)
        self.assertEqual(dl.color_hint, "red")


class TestReviewComment(unittest.TestCase):
    def test_create(self):
        c = ReviewComment(1, "Alice", CommentType.LINE_COMMENT, body="Good")
        self.assertEqual(c.author, "Alice")

    def test_type_icon(self):
        c = ReviewComment(1, "A", CommentType.SUGGESTION)
        self.assertEqual(c.type_icon, "💡")

    def test_severity_icon(self):
        c = ReviewComment(1, "A", severity=Severity.CRITICAL)
        self.assertEqual(c.severity_icon, "🚨")


class TestInlineSuggestion(unittest.TestCase):
    def test_create(self):
        s = InlineSuggestion(10, "old", "new", "Fix", False)
        self.assertEqual(s.status_icon, "💡")

    def test_applied(self):
        s = InlineSuggestion(10, "old", "new", applied=True)
        self.assertEqual(s.status_icon, "✅")


class TestFileDiff(unittest.TestCase):
    def test_change_summary(self):
        fd = FileDiff("test.py", additions=10, deletions=5)
        self.assertEqual(fd.change_summary, "+10 -5")


class TestPullRequest(unittest.TestCase):
    def test_create(self):
        pr = PullRequest(1, "Test PR", author="dev")
        self.assertEqual(pr.title, "Test PR")

    def test_approval_count(self):
        pr = PullRequest(1, "T", reviewers=[
            Reviewer("A", ReviewStatus.APPROVED, True),
            Reviewer("B", ReviewStatus.PENDING),
        ])
        self.assertEqual(pr.approval_count, 1)


class TestCodeReview(unittest.TestCase):
    def setUp(self):
        self.cr = CodeReview()

    def test_initial_state(self):
        self.assertGreater(self.cr.total_prs, 0)
        self.assertGreater(self.cr.open_prs, 0)

    def test_selected_pr(self):
        pr = self.cr.selected_pr
        self.assertIsNotNone(pr)

    def test_select_pr(self):
        self.cr.select_pr(1)
        self.assertEqual(self.cr._selected_pr, 1)

    def test_approve(self):
        self.cr.select_pr(2)
        self.cr.approve_pr()
        self.assertEqual(self.cr.selected_pr.status, ReviewStatus.APPROVED)

    def test_request_changes(self):
        self.cr.select_pr(0)
        self.cr.request_changes()
        self.assertEqual(self.cr.selected_pr.status, ReviewStatus.CHANGES_REQUESTED)

    def test_file_diffs(self):
        self.assertGreater(len(self.cr._file_diffs), 0)

    def test_render(self):
        lines = self.cr.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("CODE REVIEW" in l for l in lines))


# ==================== MusicServer Tests ====================

class TestTrack(unittest.TestCase):
    def test_duration_str(self):
        t = Track(1, "T", "A", duration_s=243)
        self.assertEqual(t.duration_str, "4:03")

    def test_like_icon(self):
        t = Track(1, "T", "A", liked=True)
        self.assertEqual(t.like_icon, "❤️")

    def test_artist_title(self):
        t = Track(1, "Song", "Artist")
        self.assertEqual(t.artist_title, "Artist — Song")


class TestPlaylist(unittest.TestCase):
    def test_track_count(self):
        p = Playlist(1, "Test", tracks=[Track(1, "A", "B"), Track(2, "C", "D")])
        self.assertEqual(p.track_count, 2)

    def test_total_duration(self):
        p = Playlist(1, "T", tracks=[Track(1, "A", "B", 300)])
        self.assertIn("min", p.total_duration)


class TestAudioEQBand(unittest.TestCase):
    def test_bar(self):
        b = AudioEQBand(gain=0.0)
        bar = b.bar
        self.assertIn("█", bar)

    def test_gain_str(self):
        b = AudioEQBand(gain=3.5)
        self.assertEqual(b.gain_str, "+3.5dB")


class TestLyrics(unittest.TestCase):
    def test_current_line(self):
        ly = Lyrics(lines=[
            LyricLine(0, "Line 1"),
            LyricLine(5, "Line 2"),
            LyricLine(10, "Line 3"),
        ])
        self.assertEqual(ly.get_current_line(3), "Line 1")
        self.assertEqual(ly.get_current_line(7), "Line 2")


class TestMusicServer(unittest.TestCase):
    def setUp(self):
        self.ms = MusicServer()

    def test_initial_state(self):
        self.assertGreater(self.ms.total_tracks, 0)
        self.assertIsNotNone(self.ms.current_track)

    def test_progress_bar(self):
        bar = self.ms.progress_bar
        self.assertIn("█", bar)

    def test_volume_bar(self):
        bar = self.ms.volume_bar
        self.assertIn("█", bar)

    def test_position_str(self):
        ps = self.ms.position_str
        self.assertIn(":", ps)

    def test_toggle_play(self):
        old = self.ms._playing
        self.ms.toggle_play()
        self.assertNotEqual(self.ms._playing, old)

    def test_next_track(self):
        old_id = self.ms.current_track.id
        self.ms.next_track()
        # may or may not change depending on queue

    def test_playlists(self):
        self.assertGreater(len(self.ms._playlists), 0)

    def test_eq_bands(self):
        self.assertGreater(len(self.ms._eq_bands), 0)

    def test_lyrics(self):
        self.assertIsNotNone(self.ms._lyrics)

    def test_queue(self):
        self.assertGreater(len(self.ms._queue), 0)

    def test_render(self):
        lines = self.ms.render()
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("MUSIC SERVER" in l for l in lines))


class TestRepeatMode(unittest.TestCase):
    def test_values(self):
        self.assertEqual(RepeatMode.OFF.value, "Off")
        self.assertEqual(RepeatMode.ALL.value, "All")
        self.assertEqual(RepeatMode.ONE.value, "One")


if __name__ == "__main__":
    unittest.main()
