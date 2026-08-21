"""Tests for splitting multiple players out of one uploaded document."""
from backend.services.files import group_by_player


def doc(text: str, name: str = "notes.txt"):
    return group_by_player([(name, text.encode())])


class TestPlayerSplit:
    def test_single_player_document_is_untouched(self):
        g = doc("Player: Tarik Skubal - LHP\nSat 92-95 with a plus changeup.")
        assert list(g) == ["notes"]

    def test_horizontal_rule_splits_players(self):
        g = doc(
            "Player: Tarik Skubal - LHP\nPlus changeup.\n"
            "\n---\n\n"
            "Player: Jacob Misiorowski - RHP\nBig arm, loose command."
        )
        assert set(g) == {"Tarik Skubal", "Jacob Misiorowski"}

    def test_each_section_keeps_only_its_own_evidence(self):
        # A grade must never be built from another player's notes.
        g = doc(
            "Player: Tarik Skubal - LHP\nPlus changeup.\n---\n"
            "Player: Jacob Misiorowski - RHP\nBig arm."
        )
        assert "Big arm" not in g["Tarik Skubal"]
        assert "changeup" not in g["Jacob Misiorowski"]

    def test_name_line_wins_over_positional_label(self):
        g = doc("Name: Mason Miller\nUpper 90s.\n---\nName: Josh Hader\nDeception.")
        assert set(g) == {"Mason Miller", "Josh Hader"}

    def test_sections_without_a_name_fall_back_to_indexed_labels(self):
        g = doc("Sat 94-96, good life.\n---\nSlider is short.")
        assert set(g) == {"notes (1)", "notes (2)"}

    def test_longer_rules_and_padding_still_split(self):
        g = doc("Player: A\nnote a\n   -----   \nPlayer: B\nnote b")
        assert set(g) == {"A", "B"}

    def test_trailing_rule_does_not_create_an_empty_player(self):
        g = doc("Player: A\nnote a\n---\nPlayer: B\nnote b\n---\n")
        assert set(g) == {"A", "B"}

    def test_position_suffix_is_trimmed_from_the_label(self):
        g = doc("Player: Tarik Skubal - LHP\na\n---\nPlayer: Mason Miller - RHP\nb")
        assert set(g) == {"Tarik Skubal", "Mason Miller"}
