# task_manager.py

class TaskBoard:
    def __init__(self):
        self.tasks = []
        self.board_title = ""
        self.owner_name = ""
        self.owner_email = ""
        self.archived_count = 0

    def add_task(self, title, priority):
        self.tasks.append({"title": title, "priority": priority, "done": False})

    def remove_task(self, title):
        self.tasks = [t for t in self.tasks if t["title"] != title]

    def complete_task(self, title):
        for t in self.tasks:
            if t["title"] == title:
                t["done"] = True

    def archive_completed(self):
        remaining = [t for t in self.tasks if not t["done"]]
        self.archived_count += len(self.tasks) - len(remaining)
        self.tasks = remaining

    def set_owner(self, name, email):
        self.owner_name = name
        self.owner_email = email

    def get_owner_summary(self):
        return f"{self.owner_name} <{self.owner_email}>"

    def set_title(self, title):
        self.board_title = title

    def render_title(self):
        return f"=== {self.board_title} ==="


class TaskExporter:
    def export(self, board):
        lines = []
        lines.append(board.render_title())
        for t in board.tasks:
            lines.append(f"{t['title']} - {'done' if t['done'] else 'pending'}")
        lines.append(f"Owner: {board.get_owner_summary()}")
        lines.append(f"Archived: {board.archived_count}")
        return "\n".join(lines)