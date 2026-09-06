"""Tests for Nyrqis SDK scaffolding tool."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nyrqis_sdk.scaffold import generate_project, TEMPLATES


class TestTemplates(unittest.TestCase):
    """Test template definitions."""

    def test_templates_exist(self):
        """All expected templates are defined."""
        self.assertIn("app", TEMPLATES)
        self.assertIn("shell", TEMPLATES)
        self.assertIn("rust", TEMPLATES)

    def test_template_descriptions(self):
        """All templates have descriptions."""
        for name, tmpl in TEMPLATES.items():
            self.assertIn("description", tmpl)
            self.assertTrue(len(tmpl["description"]) > 0)

    def test_template_files(self):
        """All templates have files."""
        for name, tmpl in TEMPLATES.items():
            self.assertIn("files", tmpl)
            self.assertTrue(len(tmpl["files"]) > 0)


class TestGenerateProject(unittest.TestCase):
    """Test project generation."""

    def setUp(self):
        """Create temp directory for test projects."""
        self.tmpdir = tempfile.mkdtemp(prefix="nyrqis-sdk-test-")

    def tearDown(self):
        """Clean up test projects."""
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_app_template(self):
        """Generate app project from template."""
        project_dir = generate_project(
            name="test-app",
            template="app",
            description="A test application",
            output_dir=self.tmpdir,
        )
        self.assertTrue(project_dir.exists())
        self.assertTrue((project_dir / "pyproject.toml").exists())
        self.assertTrue((project_dir / "test_app" / "__init__.py").exists())
        self.assertTrue((project_dir / "test_app" / "main.py").exists())
        self.assertTrue((project_dir / "tests" / "test_main.py").exists())
        self.assertTrue((project_dir / "README.md").exists())

    def test_generate_shell_template(self):
        """Generate shell design project."""
        project_dir = generate_project(
            name="test-shell",
            template="shell",
            description="A test shell design",
            output_dir=self.tmpdir,
        )
        self.assertTrue(project_dir.exists())
        self.assertTrue((project_dir / "design.nstudio").exists())
        self.assertTrue((project_dir / "README.md").exists())

    def test_generate_rust_template(self):
        """Generate Rust library project."""
        project_dir = generate_project(
            name="test-lib",
            template="rust",
            description="A test library",
            output_dir=self.tmpdir,
        )
        self.assertTrue(project_dir.exists())
        self.assertTrue((project_dir / "Cargo.toml").exists())
        self.assertTrue((project_dir / "src" / "lib.rs").exists())
        self.assertTrue((project_dir / "README.md").exists())

    def test_invalid_template_raises(self):
        """Invalid template raises ValueError."""
        with self.assertRaises(ValueError):
            generate_project(
                name="test",
                template="invalid",
                output_dir=self.tmpdir,
            )

    def test_existing_directory_raises(self):
        """Existing directory raises FileExistsError."""
        project_dir = Path(self.tmpdir) / "existing"
        project_dir.mkdir()
        with self.assertRaises(FileExistsError):
            generate_project(
                name="existing",
                template="app",
                output_dir=self.tmpdir,
            )

    def test_content_formatting(self):
        """Generated content is properly formatted."""
        project_dir = generate_project(
            name="my-app",
            template="app",
            description="My awesome app",
            output_dir=self.tmpdir,
        )
        # Check pyproject.toml has project name
        pyproject = (project_dir / "pyproject.toml").read_text()
        self.assertIn("my-app", pyproject)

    def test_nested_directories_created(self):
        """Nested directories are created automatically."""
        project_dir = generate_project(
            name="test-nested",
            template="app",
            output_dir=self.tmpdir,
        )
        # Check that nested dirs exist
        self.assertTrue((project_dir / "test_nested").is_dir())
        self.assertTrue((project_dir / "tests").is_dir())


class TestCLIMain(unittest.TestCase):
    """Test CLI entry point."""

    def test_cli_importable(self):
        """CLI module is importable."""
        from nyrqis_sdk.cli import main
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
