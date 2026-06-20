from config import FEATURES, SENTRY_DSN


def init_sentry() -> None:
    if not FEATURES["sentry"]:
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        print("[sentry] Initialized.")
    except Exception as e:
        print(f"[sentry] Init failed (non-fatal): {e}")
