"""Pure skill-order aggregation (stats.ability_order): opening sequence, first-
maxed ability, and the wins/losses split gating. No DB/HTTP, no verdict math."""
from stats import VERDICT_FLOOR
from stats.ability_order import Game, summarize

# Ability ids used across the games.
Q, W, E, R = 1, 2, 3, 4


def _game(won, points):
    return Game(won=won, points=tuple(points))


def _maxed_seq(first, *rest):
    """A full game: `first` reaches level 4 (maxed first), the rest fill in."""
    return [first, first, first, first, *rest]


def test_modal_opening_is_most_common_first_four():
    games = [
        _game(True, [Q, W, Q, E, R]),
        _game(False, [Q, W, Q, E, W]),   # same opening Q W Q E
        _game(True, [W, Q, W, E, R]),    # different opening
    ]
    out = summarize(games)
    assert out["opening"]["sequence"] == [Q, W, Q, E]
    assert out["opening"]["games"] == 2 and out["opening"]["considered"] == 3


def test_first_maxed_is_ability_reaching_level_four_first():
    games = [_game(True, _maxed_seq(Q, W, E, R)),      # Q maxed first
             _game(True, _maxed_seq(Q, W, E, R)),
             _game(False, _maxed_seq(W, Q, E, R))]     # W maxed first
    out = summarize(games)
    assert out["first_maxed"]["ability_id"] == Q
    assert out["first_maxed"]["games"] == 2 and out["first_maxed"]["considered"] == 3


def test_split_hidden_below_floor_on_either_side():
    # Plenty of wins, but only one loss -> the split must stay hidden.
    games = [_game(True, [Q, W, E, R]) for _ in range(VERDICT_FLOOR + 2)]
    games.append(_game(False, [W, Q, E, R]))
    out = summarize(games)
    assert out["split"] is None
    assert out["games"] == VERDICT_FLOOR + 3        # overall still reported


def test_split_shown_when_each_side_meets_floor():
    games = ([_game(True, [Q, W, E, R]) for _ in range(VERDICT_FLOOR)]
             + [_game(False, [W, Q, E, R]) for _ in range(VERDICT_FLOOR)])
    out = summarize(games)
    assert out["split"] is not None
    assert out["split"]["wins"]["opening"]["sequence"] == [Q, W, E, R]
    assert out["split"]["losses"]["opening"]["sequence"] == [W, Q, E, R]
    assert out["split"]["wins"]["games"] == VERDICT_FLOOR


def test_tolerates_short_game_and_more_than_four_abilities():
    games = [
        _game(True, [Q, W]),                       # short: no full opening, none maxed
        _game(True, _maxed_seq(Q, W, E, R, 5, 6)), # 6 distinct abilities, Q maxed first
    ]
    out = summarize(games)
    # Only the second game qualifies for either fact.
    assert out["opening"]["considered"] == 1 and out["opening"]["sequence"] == [Q, Q, Q, Q]
    assert out["first_maxed"]["considered"] == 1 and out["first_maxed"]["ability_id"] == Q
    assert out["games"] == 2


def test_empty_games_yields_nulls():
    out = summarize([])
    assert out["games"] == 0
    assert out["opening"] is None and out["first_maxed"] is None and out["split"] is None
