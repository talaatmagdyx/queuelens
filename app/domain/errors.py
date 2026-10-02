class UnsafeToBrowse(RuntimeError):
    """Browsing this queue would count as deliveries and could drop messages — or, on a
    quorum queue deeper than one scan, reorder it."""
