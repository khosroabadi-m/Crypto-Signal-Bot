"""profiles.py — تعریف همه‌ی پروفایل‌های سیگنال موتور

این فایل فقط نام‌گذاری و شناسنامه‌ی هر پروفایل را نگه می‌دارد.
منطق هر موتور در پوشه‌ی اختصاصی خودش در `engine/` قرار دارد و بدون
هیچ تغییری کپی شده است.

۳ موتور فعال:
  ۱. PentaSignal v3.5.5    — ۵ سناریو (F1-F5) روی Binance
  ۲. Titan Signal v4.2.0   — ۳ سناریو فعال (S1, S3, B1) روی Binance
  ۳. KhosroSignalAnalyzerBot v11.3.0 — موتور SMC + AI Committee روی Binance

قوانین و پارامترهای هر موتور «کدامقدس» است و نباید تغییر کند.
اگر قوانین یک موتور در آینده تغییر کرد، فقط source code آن را در
پوشه‌ی اختصاصی‌اش به‌روز کنید — این فایل فقط شناسنامه است.
"""

# ============================================================================
# ۱) PentaSignal v3.5.5 — ۵ سناریو (F1-F5)
#    منبع: PentaSignal/pentasignal/scenarios.py (بدون تغییر)
#    صرافی: Binance public API
# ============================================================================
PENTA_PROFILES = {
    "PENTA_F1": {
        "id": "PENTA_F1",
        "name_fa": "پنتا — کتاب قانون خسرو (F1)",
        "name_en": "Penta - KhosroAiTrader Rule Book v1 (F1)",
        "engine": "PentaSignal",
        "engine_version": "3.5.5",
        "scenario_id": "F1",
        "direction": "BOTH",
        "exchange": "Binance",
        "base_tf": "1h",
        "desc_fa": (
            "پکیج کامل KhosroAiTrader: RuleSignalEngine + MarketDataHub + RiskEngine "
            "بدون بازنویسی منطق؛ رأی وزن‌دار با ساختار EMA20/50/200، RSI، MACD، "
            "مومنتوم و حجم."
        ),
        "params_summary": {
            "min_confidence": 45,
            "min_score_gap": 15,
            "atr_sl": 1.5,
            "tp_atr": 3.0,
            "cooldown_h": 12,
        },
    },
    "PENTA_F2": {
        "id": "PENTA_F2",
        "name_fa": "پنتا — بیمه‌ی کرش (F2)",
        "name_en": "Penta - Crash Insurance Short (F2)",
        "engine": "PentaSignal",
        "engine_version": "3.5.5",
        "scenario_id": "F2",
        "direction": "SHORT",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "فقط در کرش واقعی (افت ≥۸٪ BTC از سقف ۹۶ کندلی) شورتِ شکست ۸ کندلی "
            "با استاپ سربه‌سر خودکار."
        ),
        "params_summary": {
            "bk_arm_r": 1.5,
            "sl_atr": 3.5,
            "dd_gate": 0.08,
            "break_lookback": 8,
            "cooldown_h": 24,
        },
    },
    "PENTA_F3": {
        "id": "PENTA_F3",
        "name_fa": "پنتا — پول‌بک لانگ در روند (F3)",
        "name_en": "Penta - Trend Pullback Long (F3)",
        "engine": "PentaSignal",
        "engine_version": "3.5.5",
        "scenario_id": "F3",
        "direction": "LONG",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "در روند صعودی (شیب EMA50 ≥۱.۲٪ در ۲۴ کندل)، بازگشت به EMA21 با "
            "کندل برگشتی = ورود؛ تریلینگ پهن 5×ATR."
        ),
        "params_summary": {
            "trail_atr": 5.0,
            "sl_atr": 2.5,
            "slope_min": 0.012,
            "touch_atr": 0.2,
            "cooldown_h": 24,
        },
    },
    "PENTA_F4": {
        "id": "PENTA_F4",
        "name_fa": "پنتا — ریباند-شورت جدیدها (F4)",
        "name_en": "Penta - New-Coin Rebound Short (F4)",
        "engine": "PentaSignal",
        "engine_version": "3.5.5",
        "scenario_id": "F4",
        "direction": "SHORT",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "در افت BTC و افت ≥۸٪ خود نماد، پاداشِ بازگشت به EMA21 با کندل "
            "رد شدن = شورت؛ استاپ سربه‌سر خودکار."
        ),
        "params_summary": {
            "bk_arm_r": 1.0,
            "sl_atr": 2.5,
            "coin_dd": 0.08,
            "touch_atr": 0.2,
            "cooldown_h": 8,
        },
    },
    "PENTA_F5": {
        "id": "PENTA_F5",
        "name_fa": "پنتا — کپیتولیشن لانگ (F5)",
        "name_en": "Penta - Capitulation Long (F5)",
        "engine": "PentaSignal",
        "engine_version": "3.5.5",
        "scenario_id": "F5",
        "direction": "LONG",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "کف‌گیری حجم‌محور در پنیک جدیدها (حجم ≥1.5×، دامنه ≥1.8×ATR، "
            "کلوز نزدیک کف)؛ بعد از +1R سربه‌سر."
        ),
        "params_summary": {
            "bk_arm_r": 1.0,
            "sl_atr": 2.5,
            "vol_x": 1.5,
            "range_x": 1.8,
            "cooldown_h": 24,
        },
    },
}


# ============================================================================
# ۲) Titan Signal v4.2.0 (OperationalWindows) — ۳ سناریو فعال
#    منبع: Titan-Signal-main/titansignal/config.py (بدون تغییر)
#    صرافی: Binance (با فرمت -USDT)
#    فعال: ['S1', 'S3', 'B1']  (S2 و B2 disabled)
# ============================================================================
TITAN_PROFILES = {
    "TITAN_S1": {
        "id": "TITAN_S1",
        "name_fa": "تایتان — پرومتئوس (S1)",
        "name_en": "Titan - Prometheus (S1)",
        "engine": "TitanSignal",
        "engine_version": "4.2.0",
        "codename": "OperationalWindows",
        "scenario_id": "S1",
        "direction": "LONG",
        "behavior_fa": "سوار ترند صعودی",
        "behavior_en": "Long Trend Rider",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "تایتان پیش‌اندیشی - با دید آینده‌نگر ترندهای صعودی را شکار می‌کند. "
            "RSI zone filter فقط روی S1 اعمال می‌شود."
        ),
        "params_summary": {
            "sl_pct": 0.040,
            "trail_activate": 0.003,
            "trail_lock": 0.90,
            "max_hold_candles": 72,
            "score_min": 50,
            "cooldown_s": 14400,
            "weight_threshold": 0.40,
            "min_passed_rules": 5,
            "extra_filters": {"min_adx": 25, "require_di_align": True},
            "v31_filter": "RSI zone only",
        },
    },
    "TITAN_S3": {
        "id": "TITAN_S3",
        "name_fa": "تایتان — کرونوس (S3)",
        "name_en": "Titan - Cronus (S3)",
        "engine": "TitanSignal",
        "engine_version": "4.2.0",
        "codename": "OperationalWindows",
        "scenario_id": "S3",
        "direction": "LONG",
        "behavior_fa": "فرمانروای صعودی",
        "behavior_en": "Long-Only Ruler",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "پادشاه تایتان‌ها - فقط ترندهای صعودی را شکار می‌کند (SHORT غیرفعال). "
            "بدون فیلتر V3.1 — منطق خالص V1."
        ),
        "params_summary": {
            "sl_pct": 0.040,
            "trail_activate": 0.003,
            "trail_lock": 0.90,
            "max_hold_candles": 120,
            "score_min": 50,
            "cooldown_s": 21600,
            "weight_threshold": 0.40,
            "min_passed_rules": 5,
        },
    },
    "TITAN_B1": {
        "id": "TITAN_B1",
        "name_fa": "تایتان — هایپریون (B1)",
        "name_en": "Titan - Hyperion (B1)",
        "engine": "TitanSignal",
        "engine_version": "4.2.0",
        "codename": "OperationalWindows",
        "scenario_id": "B1",
        "direction": "LONG",
        "behavior_fa": "نورافکن صعودی",
        "behavior_en": "Bullish Light Bringer",
        "exchange": "Binance",
        "base_tf": "30m",
        "desc_fa": (
            "تایتان نور - با درک وسیع مسیرهای صعودی را روشن می‌کند. "
            "range_filter_mode=OR برای دید گسترده‌تر."
        ),
        "params_summary": {
            "sl_pct": 0.040,
            "trail_activate": 0.003,
            "trail_lock": 0.90,
            "max_hold_candles": 96,
            "score_min": 50,
            "cooldown_s": 14400,
            "weight_threshold": 0.40,
            "min_passed_rules": 5,
            "range_filter_mode": "OR",
        },
    },
}

# ============================================================================
# ۳) KhosroSignalAnalyzerBot v11.3.0 — موتور SMC + AI Committee
#    منبع: KhosroSignalAnalyzerBot/smc.py و ai_committee.py (بدون تغییر)
#    صرافی: Binance (data-api.binance.vision)
#    ۱۰ مؤلفه SMC + اجماع AI
# ============================================================================
KHOSRO_PROFILES = {
    "KHOSRO_SMC": {
        "id": "KHOSRO_SMC",
        "name_fa": "خسرو — موتور SMC + اجماع AI",
        "name_en": "Khosro - SMC Confluence + AI Committee",
        "engine": "KhosroSignalAnalyzerBot",
        "engine_version": "11.3.0",
        "strategy_name": "Khosro Confluence Engine + AI Committee",
        "scenario_id": "SMC",
        "direction": "BOTH",
        "exchange": "Binance (data-api.binance.vision)",
        "base_tf": "30m",
        "htf_tfs": ["1h", "4h"],
        "desc_fa": (
            "موتور کانفلوئنس خسرو با ۱۰ مؤلفه SMC: order_flow, volume_profile, "
            "sweep, liquidity, FVG, fibonacci, supply_demand, moving_average, "
            "macd, rsi + اجماع مدل‌های AI."
        ),
        "params_summary": {
            "LONG_SIGNAL_SCORE": 50,
            "SHORT_SIGNAL_SCORE": 55,
            "LONG_HTF_MIN": 0.25,
            "SHORT_HTF_MIN": 0.45,
            "weights": {
                "ai_committee": 25,
                "order_flow": 14,
                "volume_profile": 11,
                "sweep": 10,
                "moving_average": 5,
                "macd": 4,
                "rsi": 3,
            },
            "stop_max_pct": 0.030,
            "stop_min_pct": 0.005,
            "struct_stop_max_atr": 2.5,
            "signal_max_age_hours": 96,
        },
    },
}

# ============================================================================
# جدول master همه‌ی پروفایل‌ها
# ============================================================================
ALL_PROFILES = {**PENTA_PROFILES, **TITAN_PROFILES, **KHOSRO_PROFILES}

# پروفایل‌های فعال (در تولید سیگنال استفاده می‌شوند)
ACTIVE_PROFILES = list(ALL_PROFILES.keys())

# گروه‌بندی برای نمایش در UI
PROFILE_GROUPS = {
    "PentaSignal v3.5.5 (Binance)": list(PENTA_PROFILES.keys()),
    "Titan Signal v4.2.0 (Binance)": list(TITAN_PROFILES.keys()),
    "Khosro v11.3.0 (SMC + AI)": list(KHOSRO_PROFILES.keys()),
}


def get_profile(profile_id: str) -> dict:
    """Get profile metadata by id."""
    return ALL_PROFILES.get(profile_id, {
        "id": profile_id,
        "name_fa": profile_id,
        "name_en": profile_id,
        "engine": "Unknown",
    })


def get_profile_name_fa(profile_id: str) -> str:
    """Get Persian name of profile."""
    p = get_profile(profile_id)
    return p.get("name_fa", profile_id)


def get_profile_name_en(profile_id: str) -> str:
    """Get English name of profile."""
    p = get_profile(profile_id)
    return p.get("name_en", profile_id)


def get_profile_group(profile_id: str) -> str:
    """Get the engine group name for a profile."""
    p = get_profile(profile_id)
    return p.get("engine", "Unknown")


def list_profiles_for_ui() -> list:
    """List all profiles for UI dropdown."""
    out = []
    for group, ids in PROFILE_GROUPS.items():
        out.append({"group": group, "profiles": []})
        for pid in ids:
            p = ALL_PROFILES[pid]
            out[-1]["profiles"].append({
                "id": pid,
                "name_fa": p["name_fa"],
                "name_en": p["name_en"],
                "direction": p.get("direction", "BOTH"),
                "exchange": p.get("exchange", "—"),
                "desc_fa": p.get("desc_fa", ""),
            })
    return out
