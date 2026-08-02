"""A small stateless text-utility class, for variety alongside the domain
classes in billing.py and accounts.py."""


class SlugGenerator:
    def slugify(self, text):
        cleaned = text.strip().lower()
        cleaned = cleaned.replace(" ", "-")
        return "".join(ch for ch in cleaned if ch.isalnum() or ch == "-")

    def truncate(self, text, max_length):
        if len(text) <= max_length:
            return text
        return text[:max_length].rstrip("-") + "..."
