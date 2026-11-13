from clean_acceptance import ROOT, capture_webapp


if __name__ == "__main__":
    site_code = (ROOT / ".runtime" / "acceptance-site-code").read_text(encoding="ascii").strip()
    capture_webapp(site_code)
