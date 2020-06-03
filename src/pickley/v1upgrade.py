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
        self.cfg = cfg
        self.installed = []
        if not os.path.isdir(cfg.meta.path):
            return

        for fname in os.listdir(cfg.meta.path):
            if fname == "pickley":
                continue

            old_manifest = cfg.meta.full_path(fname, ".current.json")
            old_entrypoints = cfg.meta.full_path(fname, ".entry-points.json")
            eps = runez.read_json(old_entrypoints, default=None)
            if eps and os.path.exists(old_manifest):
                v1 = V1Install(fname, eps)
                self.installed.append(v1)

    def clean_old_files(self):
        for fname in os.listdir(self.cfg.meta.path):
            fpath = os.path.join(self.cfg.meta.path, fname)
            if fname == "_venvs":
                runez.delete(fpath)
                continue

            if not os.path.isdir(fpath):
                continue

            runez.delete(os.path.join(fpath, ".current.json"))
            runez.delete(os.path.join(fpath, ".entry-points.json"))
            runez.delete(os.path.join(fpath, ".latest.json"))
            runez.delete(os.path.join(fpath, ".ping"))

            remaining = os.listdir(fpath)
            if fname != "pickley" and not remaining:
                runez.delete(fpath)
