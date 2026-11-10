"""Record one doubles match and print the results. Run with: python examples/basic_match.py"""

from datetime import date, datetime

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    GameFormat,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
)


def main() -> None:
    cfg = Config()
    engine = RatingEngine(cfg)

    # Verified snapshot from a standard external rating system. Its
    # reliability is kept as labeled provenance, not converted to OPEN RD.
    engine.seed_external_rating(
        "player_a",
        RatingFormat.DOUBLES,
        ExternalRatingSnapshot(
            source="external_standard",
            rating=4.25,
            reliability=85,
            captured_on=date(2026, 10, 1),
            external_player_id="ext-12345",
            evidence_reference="verified-import-record-001",
        ),
    )
    engine.seed_external_rating(
        "player_b",
        RatingFormat.DOUBLES,
        ExternalRatingSnapshot(
            source="external_standard",
            rating=4.05,
            reliability=78,
            captured_on=date(2026, 10, 1),
            external_player_id="ext-67890",
        ),
    )
    engine.seed_provisional_rating("player_c", RatingFormat.DOUBLES)
    engine.seed_provisional_rating("player_d", RatingFormat.DOUBLES)

    match = Match(
        match_id="event-2026-10-29-court-3-match-7",
        team_a=("player_a", "player_c"),
        team_b=("player_b", "player_d"),
        games=((11, 8), (9, 11), (11, 7)),
        played_on=date(2026, 10, 29),
        rating_format=RatingFormat.DOUBLES,
        match_type=MatchType.LEAGUE,
        best_of=3,
        game_format=GameFormat(ScoringType.SIDE_OUT, points_to=11),
        played_at=datetime(2026, 10, 29, 19, 30),
    )

    result = engine.record(match)
    print("Match result:", result)
    for player_id in ("player_a", "player_b", "player_c", "player_d"):
        print(engine.player_summary(player_id, RatingFormat.DOUBLES))
    print("Config", cfg.version, cfg.fingerprint())


if __name__ == "__main__":
    main()
