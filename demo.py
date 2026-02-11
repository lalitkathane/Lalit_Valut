import os
from pathlib import Path


def generate_project_structure(root_dir=".", ignore_dirs=None, ignore_files=None, output_file="project_structure.txt"):
    """
    Generate a visual representation of project structure, ignoring specified dirs and files.

    Args:
        root_dir: Root directory to start from (default: current directory)
        ignore_dirs: Set of directory names to ignore (e.g., {'__pycache__', 'cache', '.git'})
        ignore_files: Set of file patterns to ignore (e.g., {'__init__.py'})
        output_file: File to save the structure to
    """
    if ignore_dirs is None:
        ignore_dirs = {'__pycache__', 'cache', '.git', '.venv', 'venv', 'node_modules', 'dist', 'build'}

    if ignore_files is None:
        ignore_files = {'__init__.py'}

    def _walk_directory(directory, prefix="", output=None):
        entries = []
        try:
            # Get all entries, filter out ignored ones
            for entry in sorted(Path(directory).iterdir()):
                if entry.name in ignore_dirs and entry.is_dir():
                    continue
                if entry.name in ignore_files and entry.is_file():
                    continue
                entries.append(entry)
        except PermissionError:
            return

        # Sort directories first, then files
        entries.sort(key=lambda x: (x.is_file(), x.name.lower()))

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            current_prefix = "└── " if is_last else "├── "
            output.append(prefix + current_prefix + entry.name)

            if entry.is_dir():
                extension = "│   " if not is_last else "    "
                _walk_directory(entry, prefix + extension, output)

    output = [f"{Path(root_dir).name}/"]
    _walk_directory(root_dir, "", output)

    # Save to file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output))

    # Print to console
    print('\n'.join(output))
    print(f"\nStructure saved to {output_file}")


if __name__ == "__main__":
    generate_project_structure()
