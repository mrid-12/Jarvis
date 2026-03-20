import json
import os
from pathlib import Path

class MemoryManager:
    """
    Simulates a Retrieval-Augmented Generation (RAG) system.
    Loads common instructions from a local 'instructions' directory.
    """
    def __init__(self, memory_dir="instructions"):
        self.memory_dir = Path(__file__).parent / memory_dir
        self.memory_dir.mkdir(exist_ok=True)
        self.knowledge_base = {}
        self._load_instructions()

    def _load_instructions(self):
        """Loads all .txt or .json files into memory."""
        print(f"Loading instructions from {self.memory_dir}")
        for file_path in self.memory_dir.glob("*"):
            if file_path.suffix in ['.txt', '.md']:
                try:
                    with open(file_path, 'r') as f:
                        self.knowledge_base[file_path.stem] = f.read()
                except Exception as e:
                    print(f"Error loading {file_path.name}: {e}")
            elif file_path.suffix == '.json':
                 try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                        self.knowledge_base[file_path.stem] = json.dumps(data)
                 except Exception as e:
                    print(f"Error loading {file_path.name}: {e}")

    def get_relevant_instructions(self, user_query):
        """
        Retrieves instructions. For a real RAG setup, this would use vector embeddings.
        Here we use simple keyword matching to find relevant instruction files.
        """
        relevant_context = ""
        query_lower = user_query.lower()
        
        for topic, content in self.knowledge_base.items():
            if topic.lower() in query_lower:
                relevant_context += f"--- Context for {topic} ---\n{content}\n"
                
        if not relevant_context:
            return "No specific instructions found in memory for this application or game."
            
        return relevant_context
