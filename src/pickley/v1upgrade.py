import os

import runez


class V1Install(object):
    """Name and entry points of an older v1 install"""

    def __init__(self, name, entrypoints):
        self.name = name
        self.entrypoints = entrypoints

    def __repr__(self):
        return self.name


class V1Status(object):
    """Temporary scan of older v1 installs, in order to auto-upgrade them to v2"""

    def __init__(self, cfg):
        self.old_meta = os.path.join(cfg.base.path, ".pickley")
        self.installed = []
        if not os.path.isdir(self.old_meta):
            return

        for fname in os.listdir(self.old_meta):
            if fname == "pickley":
                continue

            folder = os.path.join(self.old_meta, fname)
            if not os.path.isdir(folder):
                continue

            old_manifest = os.path.join(folder, ".current.json")
            old_entrypoints = os.path.join(folder, ".entry-points.json")
            if os.path.exists(old_manifest):
                self.installed.append(V1Install(fname, runez.read_json(old_entrypoints, default=None)))
