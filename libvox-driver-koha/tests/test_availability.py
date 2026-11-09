"""Deriving "can I have this book?" from Koha's item flags.

The driver README called this a cataloguing question rather than a plumbing
one, and it is: every rule here is a judgement about what a library means by
its own data, and each one is wrong in a way a patron would notice.

The fixtures are live captures. `items_checked_out.json` in particular settles
something no argument could — a Koha item record carries `checked_out_date`
and no due date at all.
"""

from __future__ import annotations

import copy
import functools

import pytest

from libvox_driver_koha import (
    Availability,
    KohaDriver,
    NotFound,
    availability_from_items,
    item_state,
)
from libvox_driver_koha import handler as handler_module

from .conftest import CREDENTIALS, ITEMS_CHECKED_OUT, ITEMS_ON_SHELF


@pytest.fixture
def invoke(koha, monkeypatch):
    """Call `handle()` against a scripted Koha.

    The transport is bound to the driver class rather than passed in the
    request, exactly as test_handler.py does it: `_connection` refuses keys it
    does not know, and a test hook has no business in a contract that crosses a
    network boundary.
    """
    real = handler_module.KohaDriver
    monkeypatch.setattr(
        handler_module,
        "KohaDriver",
        functools.partial(real, transport=koha.transport, min_interval_s=0.0),
    )
    return handler_module.handle


def item(**overrides) -> dict:
    """One real copy, adjusted. Built from a capture, never from imagination."""
    base = copy.deepcopy(ITEMS_ON_SHELF[0])
    base.update(overrides)
    return base


# --- classifying one copy --------------------------------------------------


def test_a_clear_copy_is_on_the_shelf() -> None:
    assert item_state(ITEMS_ON_SHELF[0]) == "shelf"


def test_a_copy_with_a_checkout_date_is_out() -> None:
    assert item_state(ITEMS_CHECKED_OUT[0]) == "out"


@pytest.mark.parametrize(
    "flag", ["lost_status", "withdrawn", "damaged_status", "restricted_status"]
)
def test_a_flagged_copy_is_gone(flag: str) -> None:
    """Lost, withdrawn, damaged and restricted are all "not for you, today"."""
    assert item_state(item(**{flag: 1})) == "gone"


def test_a_lost_copy_that_is_also_out_is_gone_not_out() -> None:
    """Order matters: "due back" would promise a return that is not coming."""
    assert item_state(item(lost_status=1, checked_out_date="2024-12-24")) == "gone"


def test_reference_is_read_from_the_effective_flag() -> None:
    """Most libraries set "reference only" on the item *type*, not the item.

    Reading `not_for_loan_status` alone would miss an entire reference
    collection, and every copy in it would be offered as a loan.
    """
    assert item_state(item(effective_not_for_loan_status=1)) == "reference"
    assert item_state(item(not_for_loan_status=2)) == "reference"


def test_zero_as_a_string_still_means_fine() -> None:
    """Koha writes 0, "0", null and false for the same thing across fields."""
    assert item_state(item(lost_status="0", withdrawn=None)) == "shelf"


# --- summarizing a record --------------------------------------------------


def test_one_copy_on_the_shelf_is_available() -> None:
    result = availability_from_items(1, ITEMS_ON_SHELF)
    assert (result.status, result.copies_total, result.copies_available) == (
        "available",
        1,
        0 + 1,
    )


def test_reference_copies_are_counted_apart_from_loanable_ones() -> None:
    """The answer a four-value vocabulary cannot give on its own.

    "We have it, take it home" and "we have it, read it here" are different
    answers to the one question asked. Folding reference into `available`
    promises a loan the desk will refuse; dropping it hides a copy sitting
    right there.
    """
    result = availability_from_items(
        1, [item(), item(effective_not_for_loan_status=1), item(effective_not_for_loan_status=1)]
    )
    assert result.copies_total == 3
    assert result.copies_available == 1
    assert result.copies_reference_only == 2


def test_only_reference_copies_is_unavailable_but_not_empty_handed() -> None:
    """`unavailable` is about borrowing. The count is what stops it being a lie."""
    result = availability_from_items(1, [item(effective_not_for_loan_status=1)])
    assert result.status == "unavailable"
    assert result.copies_reference_only == 1


def test_every_copy_out_is_checked_out() -> None:
    result = availability_from_items(1, [item(checked_out_date="2024-12-24")] * 2)
    assert result.status == "checked_out"
    assert result.copies_available == 0


def test_one_copy_free_beats_three_copies_out() -> None:
    result = availability_from_items(
        1, [item(checked_out_date="2024-12-24")] * 3 + [item()]
    )
    assert result.status == "available"
    assert result.copies_available == 1


def test_a_queue_on_the_only_copy_is_on_hold() -> None:
    """"Checked out" would understate the wait by the length of the queue."""
    result = availability_from_items(
        1, [item(checked_out_date="2024-12-24", holds_count=3)]
    )
    assert result.status == "on_hold"


def test_a_record_with_no_copies_at_all_is_unavailable() -> None:
    """A catalogued record whose copies were all withdrawn is not an error."""
    result = availability_from_items(1, [])
    assert (result.status, result.copies_total) == ("unavailable", 0)


def test_the_due_date_is_never_filled_in() -> None:
    """Not an omission. Koha reports it only through /checkouts, which needs
    the `circulate` permission — and LibVox asks libraries for a read-only key
    with `catalogue` alone, because it never writes to an ILS."""
    assert availability_from_items(1, ITEMS_CHECKED_OUT).due_date is None


# --- the branch, in words --------------------------------------------------


def test_the_branch_is_not_called_location_on_the_wire() -> None:
    """The name was the whole of the bug.

    Asked whether a book was available, MARC.IA answered that it was *"na seção
    Midway"*. Midway is a branch. A seção is a shelving location — which in Koha
    is a different field (`location`, "GEN") that this contract does not carry
    at all, so the word was not merely loose, it named the wrong kind of place.

    Nothing else about that answer was wrong: the record, the counts and the
    branch were all right. A model reading a field called `location`, described
    to it as "branch or shelf location", picked the second half. Renaming it is
    the fix; a rule in the system prompt telling the model to translate a
    misleading name correctly would not have been.
    """
    payload = availability_from_items(
        1, ITEMS_ON_SHELF, library_names={"CPL": "Centerville"}
    ).as_dict()

    assert payload["library"] == "Centerville"
    assert "location" not in payload


def test_the_branch_code_is_resolved_to_a_name() -> None:
    """A patron told the book is at "CPL" has learned nothing."""
    result = availability_from_items(
        1, ITEMS_ON_SHELF, library_names={"CPL": "Centerville"}
    )
    assert result.library == "Centerville"


def test_an_unknown_branch_code_is_passed_through_rather_than_dropped() -> None:
    assert availability_from_items(1, ITEMS_ON_SHELF, library_names={}).library == "CPL"


def test_the_branch_comes_from_a_copy_a_patron_could_use() -> None:
    """A lost copy's building is not an address worth giving anyone."""
    result = availability_from_items(
        1,
        [item(lost_status=1, holding_library_id="LOST"), item(holding_library_id="CPL")],
        library_names={"CPL": "Centerville", "LOST": "Nowhere"},
    )
    assert result.library == "Centerville"


# --- through the driver and the HTTP stack ---------------------------------


def test_the_driver_asks_the_items_endpoint_and_resolves_names(koha) -> None:
    koha.items(1).libraries().shelves()
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    assert isinstance(result, Availability)
    assert result.status == "available"
    assert result.library == "Centerville"
    assert result.shelf == "General stacks"
    assert {r.url.path for r in koha.resource_requests()} == {
        "/api/v1/biblios/1/items",
        "/api/v1/libraries",
        "/api/v1/authorised_value_categories/LOC/authorised_values",
    }


def test_the_public_label_wins_over_the_staff_one(koha) -> None:
    """Koha keeps two descriptions per authorised value and the plain one is the
    staff interface's. MARC.IA is talking to a reader."""
    koha.items(1, rows=[{**ITEMS_ON_SHELF[0], "location": "REF"}]).libraries().shelves()
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    # 'Reference' is the staff label; 'Consulta local' is what the fixture's
    # library chose to show the public.
    assert result.shelf == "Consulta local"


def test_a_shelving_list_that_cannot_be_read_costs_the_name_and_not_the_answer(
    koha,
) -> None:
    """The same trade `_branch_names` makes. Knowing the shelf and not its name
    is worse than knowing both and better than knowing neither."""
    koha.items(1).libraries()  # no .shelves(): the endpoint 404s
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    assert result.status == "available"
    assert result.shelf == "GEN"


def test_a_shelving_code_with_no_entry_is_passed_through(koha) -> None:
    koha.items(1, rows=[{**ITEMS_ON_SHELF[0], "location": "ZZZ"}]).libraries().shelves()
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    assert result.shelf == "ZZZ"


def test_a_branch_and_a_shelf_are_cleaned_like_any_other_catalogued_string(
    koha,
) -> None:
    """These two never passed through `clean_text` and reach a reader whole.

    A branch and a shelving location are named by a cataloguer in the same
    interface with the same conventions as a title, and they arrive from
    endpoints of their own -- which is how they escaped the cleaning every
    field of the biblio record gets.
    """
    koha.items(1).libraries([{"library_id": "CPL", "name": '"Centerville"'}]).shelves(
        [{"value": "GEN", "opac_description": None, "description": "“General stacks”"}]
    )
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    assert result.library == "Centerville"
    assert result.shelf == "General stacks"


def test_the_branch_list_is_fetched_once_per_driver(koha) -> None:
    """One library per driver, and branches move about as often as buildings."""
    koha.items(1).items(2).libraries()
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        driver.check_availability(1)
        driver.check_availability(2)

    paths = [r.url.path for r in koha.resource_requests()]
    assert paths.count("/api/v1/libraries") == 1


def test_an_unreachable_branch_list_does_not_lose_the_answer(koha) -> None:
    """Knowing a copy is free and not which building beats knowing nothing."""
    koha.items(1).status("/api/v1/libraries", 500)
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    assert result.status == "available"
    assert result.library == "CPL"


def test_a_record_the_library_does_not_hold_is_an_error_not_a_guess(koha) -> None:
    """`NotFound`, not an empty Availability.

    Zero copies and no such record are different facts, and a caller that
    cannot tell them apart will say "we do not have it" about a catalogue it
    never actually asked."""
    koha.libraries()
    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver, pytest.raises(
        NotFound
    ):
        driver.check_availability(999)


# --- through the invocation contract ---------------------------------------


def test_check_availability_is_reachable_through_handle(koha, invoke) -> None:
    koha.items(1).libraries()
    response = invoke(
        {
            "action": "check_availability",
            "params": {"biblio_id": 1},
            "connection": dict(CREDENTIALS),
        }
    )
    assert response["ok"] is True
    assert response["data"]["copies_available"] == 1
    assert response["data"]["due_date"] is None


def test_handle_refuses_a_missing_biblio_id(invoke) -> None:
    response = invoke(
        {
            "action": "check_availability",
            "params": {},
            "connection": dict(CREDENTIALS),
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "bad_request"


def test_the_shelving_list_is_not_fetched_when_no_copy_has_one(koha) -> None:
    """A library that puts everything in one room fills in no location, and
    asking for the vocabulary anyway is a fourth sequential request to someone
    else's server for a field nobody filled in.

    That fourth request is what took an availability check on a catalogue in
    New Zealand past six seconds -- past what the caller was then willing to
    wait, so the answer arrived and was thrown away.
    """
    bare = {**ITEMS_ON_SHELF[0], "location": None, "permanent_location": None}
    koha.items(1, rows=[bare]).libraries().shelves()

    with KohaDriver(**CREDENTIALS, transport=koha.transport) as driver:
        result = driver.check_availability(1)

    assert result.status == "available"
    assert result.shelf is None
    assert "/api/v1/authorised_value_categories/LOC/authorised_values" not in {
        r.url.path for r in koha.resource_requests()
    }
