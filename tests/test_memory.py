"""Regression tests for deterministic, offline memory ranking."""

import copy
import itertools
import unittest

from loom_npc.memory import retrieve_memories


class MemoryTests(unittest.TestCase):
    def test_natural_chinese_ranks_history_above_unrelated_memories(self):
        actor = {"memories": [
            {"id": "letter", "summary": "玩家递交了失落的信。", "importance": 1, "tick": 1},
            {"id": "guard", "summary": "守卫的长剑已修好。", "importance": 10, "tick": 20},
            {"id": "weather", "summary": "今天海面风平浪静。", "importance": 9, "tick": 19},
        ]}
        for query in ("失落的信", "你还记得那封失落的信吗", "能说说失落的信后来怎么样了吗？"):
            with self.subTest(query=query):
                self.assertEqual([memory["id"] for memory in retrieve_memories(actor, query)],
                                 ["letter", "guard", "weather"])

    def test_mixed_words_normalize_case_width_and_punctuation(self):
        actor = {"memories": [
            {"id": "letter", "summary": "Ｍａｒａ在灯塔保管Ｌｅｔｔｅｒ４２。", "importance": 1, "tick": 1},
            {"id": "other", "summary": "Marauder owns Letter420.", "importance": 10, "tick": 20},
        ]}
        for query in ("你还记得MARA的letter42吗？", "ｍａｒａ，ＬＥＴＴＥＲ４２！", "Mara, letter42?"):
            with self.subTest(query=query):
                self.assertEqual(retrieve_memories(actor, query, limit=1)[0]["id"], "letter")

    def test_single_han_character_can_match_inside_summary(self):
        actor = {"memories": [
            {"id": "letter", "summary": "玩家递交信件。", "importance": 1, "tick": 1},
            {"id": "other", "summary": "守卫巡逻。", "importance": 10, "tick": 20},
        ]}
        self.assertEqual(retrieve_memories(actor, "信", limit=1)[0]["id"], "letter")

    def test_punctuation_does_not_join_unrelated_han_characters(self):
        actor = {"memories": [
            {"id": "false-match", "summary": "信，件", "importance": 10, "tick": 20},
            {"id": "letter", "summary": "信件", "importance": 1, "tick": 1},
        ]}
        self.assertEqual(retrieve_memories(actor, "信件", limit=1)[0]["id"], "letter")

    def test_relevance_counts_distinct_terms_before_importance_and_time(self):
        actor = {"memories": [
            {"id": "both", "summary": "灯塔的信件", "importance": 1, "tick": 1},
            {"id": "one", "summary": "信件信件信件", "importance": 10, "tick": 20},
        ]}
        for query in ("灯塔 信件", "灯塔 信件 信件 信件"):
            with self.subTest(query=query):
                self.assertEqual([memory["id"] for memory in retrieve_memories(actor, query)],
                                 ["both", "one"])

    def test_ties_and_zero_matches_are_independent_of_storage_order(self):
        memories = [
            {"id": "older", "summary": "信件", "importance": 2, "tick": 1},
            {"id": "a", "summary": "信件", "importance": 2, "tick": 2},
            {"id": "z", "summary": "信件", "importance": 2, "tick": 2},
            {"id": "recent", "summary": "信件", "importance": 1, "tick": 20},
        ]
        for query in ("信件", "海面风平浪静", "", " \t\n！？"):
            for permutation in itertools.permutations(memories):
                with self.subTest(query=query, order=[memory["id"] for memory in permutation]):
                    actor = {"memories": list(permutation)}
                    result = retrieve_memories(actor, query)
                    self.assertEqual([memory["id"] for memory in result], ["z", "a", "older", "recent"])
                    self.assertEqual(retrieve_memories(actor, query, limit=2), result[:2])

    def test_result_is_deeply_detached_and_actor_memories_are_not_sorted_in_place(self):
        actor = {"memories": [
            {"id": "other", "summary": "海面", "importance": 10, "tick": 20},
            {"id": "letter", "summary": "信件", "importance": 1, "tick": 1,
             "source": {"witnesses": ["mara"]}},
        ]}
        before = copy.deepcopy(actor)
        result = retrieve_memories(actor, "信件", limit=1)
        result[0]["source"]["witnesses"].append("ivo")
        self.assertEqual(actor, before)
        self.assertEqual(retrieve_memories({"memories": []}, "信件"), [])
        self.assertEqual(retrieve_memories(actor, "信件", limit=0), [])


if __name__ == "__main__":
    unittest.main()
