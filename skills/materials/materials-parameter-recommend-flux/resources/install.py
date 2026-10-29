#!/usr/bin/env python3
"""
Installation script for Preparation Recommendation System

This script sets up the complete preparation recommendation pipeline in the current project:
1. Creates directory structure
2. Copies source code files
3. Sets up data templates
4. Creates virtual environment
5. Installs dependencies

Usage:
    python install.py [--skill-root PATH]

    --skill-root: Path to the skill's root directory (optional, auto-detected if not provided)
"""

import os
import sys
import shutil
import argparse
from pathlib import Path


def get_skill_resources_path(skill_root: Path = None) -> Path:
    """Get the path to the skill's resources directory."""
    if skill_root:
        return skill_root / "resources"

    current_file = Path(__file__).resolve()
    skill_resources = current_file.parent

    if skill_resources.name == "resources" and (skill_resources.parent / "SKILL.md").exists():
        return skill_resources

    return skill_resources


def create_directory_structure(install_root):
    """Create the required directory structure under recommend_parameter/."""
    directories = [
        "src",
        "utils",
        "runner",
        "data/knowledge_base",
        "data/similar_mates",
        "data/recommand_window",
        "data/recommand_recipe",
    ]

    print("Creating directory structure...")
    for dir_path in directories:
        full_path = install_root / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {full_path}")


def copy_source_files(install_root, resources_path):
    """Copy source code files to the recommend_parameter directory."""
    print("\nCopying source files...")

    source_dirs = {
        "src": resources_path / "src",
        "utils": resources_path / "utils",
        "runner": resources_path / "runner",
    }

    for target_name, source_dir in source_dirs.items():
        if not source_dir.exists():
            print(f"  Warning: Source directory {source_dir} does not exist, skipping")
            continue

        target_path = install_root / target_name
        for file in source_dir.glob("*.py"):
            dest_file = target_path / file.name
            shutil.copy2(file, dest_file)
            print(f"  Copied: {dest_file}")


def copy_data_templates(install_root, resources_path):
    """Copy data template files."""
    print("\nCopying data templates...")

    template_mappings = {
        "data/similar_mates/intuition_template.jsonl":
            resources_path / "data/similar_mates/intuition_template.jsonl",
        "data/similar_mates/how_to_parse_intuition.jsonl":
            resources_path / "data/similar_mates/how_to_parse_intuition.jsonl",
        "data/recommand_window/input_template.jsonl":
            resources_path / "data/recommand_window/input_template.jsonl",
        "data/recommand_recipe/input_real.jsonl":
            resources_path / "data/recommand_recipe/input_real.jsonl",
    }

    for target, source in template_mappings.items():
        if not source.exists():
            print(f"  Warning: Template {source} does not exist, skipping")
            continue

        target_path = install_root / target
        shutil.copy2(source, target_path)
        print(f"  Copied: {target_path}")


def copy_knowledge_base(install_root, resources_path):
    """Copy knowledge base file."""
    print("\nCopying knowledge base...")

    kb_source = resources_path / "data/knowledge_base/knowledge_base_processed.jsonl"
    kb_target = install_root / "data/knowledge_base/knowledge_base_processed.jsonl"

    if not kb_source.exists():
        print(f"  Warning: Knowledge base {kb_source} does not exist")
        print("  Please prepare your knowledge base data manually!")
        kb_target.touch()
        return False

    shutil.copy2(kb_source, kb_target)
    print(f"  Copied: {kb_target}")
    return True


def create_env_file(install_root, resources_path):
    """No-op: LLM is handled by Claude directly, no .env needed."""
    pass


def setup_virtual_environment(install_root):
    """Create and activate virtual environment."""
    print("\nSetting up virtual environment...")
    venv_path = install_root / ".venv"

    if venv_path.exists():
        print(f"  Virtual environment already exists at {venv_path}")
        return

    print("  Creating virtual environment...")
    import venv
    venv.create(venv_path, with_pip=True)

    print(f"  Virtual environment created at {venv_path}")
    print("\n  To activate the virtual environment, run:")
    if sys.platform == "win32":
        print("    .venv\\Scripts\\activate")
    else:
        print("    source .venv/bin/activate")


def install_dependencies(install_root):
    """Install Python dependencies."""
    print("\nInstalling dependencies...")
    requirements_file = install_root / "requirements.txt"

    if not requirements_file.exists():
        print("  Warning: requirements.txt not found in install root")
        return

    import subprocess
    venv_python = install_root / ".venv/bin/python"
    if sys.platform == "win32":
        venv_python = install_root / ".venv/Scripts/python"

    if not venv_python.exists():
        print("  Warning: Virtual environment not found. Run setup first.")
        return

    try:
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
            check=True,
            capture_output=True
        )
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", str(requirements_file)],
            check=True
        )
        print("  Dependencies installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"  Error installing dependencies: {e}")


def main():
    """Main installation function."""
    parser = argparse.ArgumentParser(
        description="Install Preparation Recommendation System to your project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect skill root (run from skill's resources directory)
  python install.py

  # Specify skill root explicitly
  python install.py --skill-root /path/to/skill

  # Install to specific project directory
  cd /your/project && python /path/to/skill/resources/install.py
        """
    )
    parser.add_argument(
        "--skill-root",
        type=str,
        default=None,
        help="Path to the skill's root directory (auto-detected if not provided)"
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Path to the project root directory (default: current directory)"
    )
    parser.add_argument(
        "--install-dir",
        type=str,
        default="recommend_parameter",
        help="Name of the installation directory under project root (default: recommend_parameter)"
    )

    args = parser.parse_args()

    project_root = Path(args.project_root) if args.project_root else Path.cwd()
    skill_root = Path(args.skill_root) if args.skill_root else None
    resources_path = get_skill_resources_path(skill_root)
    install_dir = args.install_dir
    install_root = project_root / install_dir

    print("=" * 60)
    print("Preparation Recommendation System - Installation")
    print("=" * 60)
    print(f"\nProject root: {project_root}")
    print(f"Installation directory: {install_root}")
    print(f"Skill resources: {resources_path}")

    if not resources_path.exists():
        print(f"\nError: Skill resources not found at {resources_path}")
        print("Please specify --skill-root if the script is not in the skill's resources directory")
        sys.exit(1)

    try:
        print(f"\nCreating installation directory: {install_root}")
        install_root.mkdir(parents=True, exist_ok=True)

        create_directory_structure(install_root)
        copy_source_files(install_root, resources_path)
        copy_data_templates(install_root, resources_path)
        copy_knowledge_base(install_root, resources_path)
        create_env_file(install_root, resources_path)
        setup_virtual_environment(install_root)
        install_dependencies(install_root)

        print("\n" + "=" * 60)
        print("Installation completed successfully!")
        print("=" * 60)

        print(f"\nNext steps:")
        print(f"1. The pipeline is ready. LLM reasoning is handled by Claude directly.")
        print(f"2. Invoke this skill in Claude Code to run the pipeline.")
        print(f"")
        print(f"Or run Python computation steps manually:")
        print(f"   cd {install_root}")
        print(f"   source .venv/bin/activate")
        print("=" * 60)

    except Exception as e:
        print(f"\nError during installation: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
