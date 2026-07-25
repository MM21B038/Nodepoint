from __future__ import annotations

import os
from typing import List


def read_document_content(file_path: str) -> str | None:
    if not os.path.exists(file_path):
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return content


def extract_files_path(workspace_path: str) -> List[str]:
    if not os.path.exists(workspace_path):
        return []

    file_paths: List[str] = []
    for root, _, files in os.walk(workspace_path):
        for file in files:
            if file.endswith((".txt", ".md")):
                file_paths.append(os.path.join(root, file))

    return file_paths
