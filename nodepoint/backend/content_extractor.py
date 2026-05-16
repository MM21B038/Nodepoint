import os
from pathlib import Path
from django.conf import settings
from nodepoint.models import Document, Workspace

def read_document_content(file_path):
    
    if not os.path.exists(file_path):
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return content

def extract_files_path(workspace_path):

    if not os.path.exists(workspace_path):
        return []

    file_paths = []
    for root, dirs, files in os.walk(workspace_path):
        for file in files:
            if file.endswith((".txt", ".md")):
                file_paths.append(os.path.join(root, file))

    return file_paths