from polymarket_scanner.manual_fill_commands import parse_structural_fill_command


def test_parse_structural_fill_command_requires_explicit_every_leg_fee():
    parsed = parse_structural_fill_command(
        "/filled 137 1=50@0.470+0.02 2=50@0.460+0"
    )
    assert parsed == (
        137,
        [
            {"leg": 1, "shares": 50.0, "avg_price": 0.47, "fee_usd": 0.02},
            {"leg": 2, "shares": 50.0, "avg_price": 0.46, "fee_usd": 0.0},
        ],
    )


def test_parse_structural_fill_command_rejects_ambiguous_or_invalid_forms():
    bad = [
        "/filled 137",
        "/filled 137 1=50@0.47",
        "/filled 137 1=50@0.47+0.01 garbage",
        "/filled 0 1=50@0.47+0.01",
        "/filled 137 0=50@0.47+0.01",
        "/filled 137 1=0@0.47+0.01",
        "/filled 137 1=50@1.00+0.01",
        "/filled 137 1=50@0.47+-1",
    ]
    for text in bad:
        assert parse_structural_fill_command(text) is None
