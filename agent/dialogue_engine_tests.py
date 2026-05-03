import sys

from call_scenario_tests import make_cfg, make_ud
from core.actions import DialogueAction
from core.dialogue_engine import DialogueEngine


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def assert_true(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


def action(flow: str, name: str) -> tuple[DialogueAction, object]:
    ud = make_ud(name, make_cfg())
    return DialogueEngine().next_action(flow, ud), ud


def test_takeaway_slot_order() -> int:
    engine = DialogueEngine()
    ud = make_ud("engine-takeaway", make_cfg())

    a1 = engine.next_action("takeaway", ud)
    assert_true("takeaway first asks order", a1.question_category == "order" and "تطلب" in a1.message, str(a1))

    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    a2 = engine.next_action("takeaway", ud)
    assert_true("takeaway after order asks name", a2.question_category == "name", str(a2))

    ud.customer_name = "أحمد"
    a3 = engine.next_action("takeaway", ud)
    assert_true("takeaway after name asks phone", a3.question_category == "phone", str(a3))

    ud.customer_phone = "01012345678"
    a4 = engine.next_action("takeaway", ud)
    assert_true("takeaway ready confirms", a4.type == "confirm" and "صح" in a4.message, str(a4))
    return 4


def test_delivery_slot_order() -> int:
    engine = DialogueEngine()
    ud = make_ud("engine-delivery", make_cfg())
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45

    a1 = engine.next_action("delivery", ud)
    assert_true("delivery asks address after order", a1.question_category == "address", str(a1))

    ud.delivery_address = "مدينة نصر شارع 1 عمارة 2"
    ud.delivery_zone = "مدينة نصر"
    a2 = engine.next_action("delivery", ud)
    assert_true("delivery asks name after address", a2.question_category == "name", str(a2))

    ud.customer_name = "سارة"
    a3 = engine.next_action("delivery", ud)
    assert_true("delivery asks phone after name", a3.question_category == "phone", str(a3))

    ud.customer_phone = "01012345678"
    a4 = engine.next_action("delivery", ud)
    assert_true("delivery confirms when ready", a4.type == "confirm" and "صح" in a4.message, str(a4))
    return 4


def test_reservation_slot_order() -> int:
    engine = DialogueEngine()
    ud = make_ud("engine-reservation", make_cfg())

    a1 = engine.next_action("reservation", ud)
    assert_true("reservation asks time", a1.question_category == "reservation_time", str(a1))

    ud.reservation_time = "بكرة الساعة 8"
    a2 = engine.next_action("reservation", ud)
    assert_true("reservation asks guests", a2.question_category == "guests", str(a2))

    ud.guests_count = 4
    a3 = engine.next_action("reservation", ud)
    assert_true("reservation asks branch", a3.question_category == "branch", str(a3))

    ud.selected_branch = "مدينة نصر"
    a4 = engine.next_action("reservation", ud)
    assert_true("reservation asks name", a4.question_category == "name", str(a4))
    return 4


def test_no_repeated_known_slot_question() -> int:
    engine = DialogueEngine()
    ud = make_ud("engine-no-known-repeat", make_cfg())
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "منى"
    ud.customer_phone = "01012345678"

    a = engine.next_action("takeaway", ud)
    assert_true("ready action not ask name", a.question_category == "confirmation" and "اسمك" not in a.message, str(a))
    assert_true("ready action not ask phone", "موبايل" not in a.message and "رقمك" not in a.message, str(a))
    return 2


def test_repeat_guard_changes_message() -> int:
    engine = DialogueEngine()
    ud = make_ud("engine-repeat-guard", make_cfg())

    a1 = engine.next_action("takeaway", ud)
    a2 = engine.next_action("takeaway", ud)
    assert_true("same category tracked", ud.question_category_history[-2:] == ["order", "order"], str(ud.question_category_history))
    assert_true("repeat message changes", a1.message != a2.message, f"{a1.message} / {a2.message}")
    assert_true("repeat still same category", a2.question_category == "order", str(a2))
    return 3


def test_handle_turn_api_matches_next_action() -> int:
    engine = DialogueEngine()
    ud1 = make_ud("engine-handle-turn-1", make_cfg())
    ud2 = make_ud("engine-handle-turn-2", make_cfg())

    a1 = engine.handle_turn("takeaway", ud1, "dummy transcript")
    a2 = engine.next_action("takeaway", ud2)
    assert_true("handle_turn returns action", isinstance(a1, DialogueAction), str(a1))
    assert_true("handle_turn matches next_action type", a1.type == a2.type, f"{a1} / {a2}")
    assert_true("handle_turn matches next_action category", a1.question_category == a2.question_category, f"{a1} / {a2}")
    return 3


TESTS = [
    test_takeaway_slot_order,
    test_delivery_slot_order,
    test_reservation_slot_order,
    test_no_repeated_known_slot_question,
    test_repeat_guard_changes_message,
    test_handle_turn_api_matches_next_action,
]


def main() -> int:
    failures: list[str] = []
    checks = 0
    for index, test in enumerate(TESTS, start=1):
        try:
            count = test()
            checks += count
            print(f"{index:02d}. {test.__name__}: PASS ({count} checks)")
        except Exception as exc:
            failures.append(f"{test.__name__}: {exc}")
            print(f"{index:02d}. {test.__name__}: FAIL - {exc}")
    print(f"DIALOGUE_ENGINE_TESTS_PASSED: {len(TESTS) - len(failures)}/{len(TESTS)}")
    print(f"DIALOGUE_ENGINE_CHECKS: {checks}")
    if failures:
        print("FAILED_DIALOGUE_ENGINE_TESTS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
