# ambiguous_envy.py

class Alpha:
    def do_a(self): pass
    def helper_a(self): pass

class Beta:
    def do_b(self): pass
    def helper_b(self): pass

class Caller:
    def __init__(self):
        self.own_data = 1

    def reach(self, alpha, beta):
        alpha.do_a()
        alpha.helper_a()
        beta.do_b()
        beta.helper_b()
        return self.own_data