"""A large, low-cohesion class: many methods, each touching its own unrelated field."""


class ReportManager:
    def __init__(self):
        self.title = ""
        self.author = ""
        self.rows = []
        self.total = 0
        self.footer = ""
        self.logo_path = ""

    def set_title(self, title):
        self.title = title

    def set_author(self, author):
        self.author = author

    def add_row(self, row):
        self.rows.append(row)

    def clear_rows(self):
        self.rows = []

    def compute_total(self):
        self.total = sum(self.rows)

    def reset_total(self):
        self.total = 0

    def set_footer(self, footer):
        self.footer = footer

    def set_logo(self, path):
        self.logo_path = path

    def render_header(self):
        return f"{self.title} by {self.author}"

    def render_footer(self):
        return self.footer

    def render_logo(self):
        return self.logo_path
