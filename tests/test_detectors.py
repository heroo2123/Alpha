from polymarket_scanner.detectors import binary_buy_both, neg_risk_underround
from polymarket_scanner.models import Book, Market


def mkt(mid="1", eid="e", neg=False, yes="y", no="n"):
    return Market(mid,eid,"event","Event",neg,"Q?","q","c",["Yes","No"],[yes,no],[0.5,0.5],None,None,1000,5000,True,False,None,"","","",[],{})


def test_binary_arb_detected():
    m = mkt()
    books = {"y": Book("y", [], [(0.40,100)]), "n": Book("n", [], [(0.40,100)])}
    s = binary_buy_both([m], books)
    assert len(s) == 1
    assert s[0].edge > 0.15


def test_neg_risk_underround():
    rows = [mkt(str(i), "e", True, f"y{i}", f"n{i}") for i in range(3)]
    books = {f"y{i}": Book(f"y{i}", [], [(0.25,100)]) for i in range(3)}
    s = neg_risk_underround(rows, books)
    assert len(s) == 1
    assert s[0].edge > 0.20
