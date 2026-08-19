"""Witness I/O. The reader is strict because a silently-coerced character would
turn a wrong coloring into an accepted one at gate time."""

import pytest

from nk2.witness import (
    WitnessFormatError,
    WitnessVerificationError,
    read_witness,
    witness_header,
    write_witness,
)


def coloring(s: str) -> list[int]:
    return [1 if c == "+" else -1 for c in s]


AVOIDING_3_2 = coloring("--++--++")


def write_raw(tmp_path, text: str):
    p = tmp_path / "w.txt"
    p.write_bytes(text.encode("ascii"))
    return p


def test_round_trip_is_identity_including_comments(tmp_path):
    p = tmp_path / "k3_l2_N8.txt"
    extra = ["found by hand", "", "second note"]
    write_witness(p, AVOIDING_3_2, 3, 2, comments=extra)
    f, comments = read_witness(p)
    assert f == AVOIDING_3_2
    assert comments == witness_header(3, 2, 8) + extra


def test_header_records_k_l_and_n(tmp_path):
    p = tmp_path / "w.txt"
    write_witness(p, AVOIDING_3_2, 3, 2)
    _, comments = read_witness(p)
    assert "k = 3" in comments
    assert "l = 2" in comments
    assert "N = 8" in comments


def test_written_bytes_are_lf_only_and_ascii(tmp_path):
    p = tmp_path / "w.txt"
    write_witness(p, AVOIDING_3_2, 3, 2, comments=["note"])
    raw = p.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"--++--++\n")
    assert all(c < 0x80 for c in raw)


def test_write_is_deterministic(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    write_witness(a, AVOIDING_3_2, 3, 2, comments=["x"])
    write_witness(b, AVOIDING_3_2, 3, 2, comments=["x"])
    assert a.read_bytes() == b.read_bytes()


def test_refuses_to_write_a_coloring_that_does_not_avoid(tmp_path):
    p = tmp_path / "bad.txt"
    with pytest.raises(WitnessVerificationError):
        write_witness(p, coloring("+++"), 3, 2)
    assert not p.exists()


def test_verify_false_writes_anyway(tmp_path):
    p = tmp_path / "unverified.txt"
    write_witness(p, coloring("+++"), 3, 2, verify=False)
    assert read_witness(p)[0] == coloring("+++")


def test_reader_rejects_zero_character(tmp_path):
    p = write_raw(tmp_path, "# k = 3\n+0+-\n")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_whitespace_inside_the_data_line(tmp_path):
    for body in ("++ --\n", "++\t--\n", " ++--\n", "++-- \n"):
        p = write_raw(tmp_path, "# note\n" + body)
        with pytest.raises(WitnessFormatError):
            read_witness(p)


def test_reader_rejects_two_data_lines(tmp_path):
    p = write_raw(tmp_path, "# note\n++--\n--++\n")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_a_comment_after_the_data_line(tmp_path):
    p = write_raw(tmp_path, "++--\n# trailing\n")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_comments_only_file(tmp_path):
    p = write_raw(tmp_path, "# k = 3\n# l = 2\n")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_empty_data_line(tmp_path):
    p = write_raw(tmp_path, "# k = 3\n\n++--\n")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_empty_file(tmp_path):
    p = write_raw(tmp_path, "")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_non_ascii(tmp_path):
    p = tmp_path / "w.txt"
    p.write_bytes(b"# note\n++\xe2\x88\x92-\n")
    with pytest.raises(WitnessFormatError):
        read_witness(p)


def test_reader_rejects_unknown_letters(tmp_path):
    # M16: a reader that maps unknown characters to '-' would accept these and
    # then agree with a witness that says something else.
    for body in ("++x-\n", "++1-\n", "+*+-\n"):
        p = write_raw(tmp_path, "# note\n" + body)
        with pytest.raises(WitnessFormatError):
            read_witness(p)


def test_reader_tolerates_crlf(tmp_path):
    # A CRLF checkout is caught by the sha256 in G2, not by the parser; the
    # parser stays interoperable with the published bundle's text format.
    p = tmp_path / "w.txt"
    p.write_bytes(b"# note\r\n++--\r\n")
    f, comments = read_witness(p)
    assert f == coloring("++--")
    assert comments == ["note"]


def test_write_rejects_a_comment_containing_a_newline(tmp_path):
    with pytest.raises(ValueError):
        write_witness(tmp_path / "w.txt", AVOIDING_3_2, 3, 2, comments=["a\nb"])
