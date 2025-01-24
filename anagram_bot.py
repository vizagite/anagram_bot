from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple
import random
import string
import numpy as np
import asyncio
import csv
import math
from collections import defaultdict, Counter

# set of other possible valid words for same letters as anagram words. other permutations
with open("other_possible_answers", 'r') as f:
    other_possible_words = set(f.read().splitlines())

def simplified_levenshtein(word1, word2):
    len1, len2 = len(word1), len(word2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

    # Initialize the first row and column
    for i in range(len1 + 1):
        dp[i][0] = i
    for j in range(len2 + 1):
        dp[0][j] = j

    # Fill the matrix
    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            if word1[i - 1] == word2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]  # No operation needed
            else:
                dp[i][j] = min(
                    dp[i - 1][j] + 1,      # Deletion
                    dp[i][j - 1] + 1,      # Insertion
                    dp[i - 1][j - 1] + 1   # Substitution
                )
    if dp[len1][len2] == 1:
        # Backtrack to find the edit
        i, j = len1, len2
        while i > 0 and j > 0:
            if word1[i - 1] == word2[j - 1]:
                i -= 1
                j -= 1
            else:
                if dp[i][j] == dp[i - 1][j] + 1:
                    return dp[i][j], word2[j]  # Deletion
                else:
                    return dp[i][j], word2[j - 1]  # Substitution
        if i > 0:
            return dp[i][j], word2[j]  # Deletion

    return dp[len1][len2], None  # No single-letter edit 
 
class AnagramDatabaseHandler:
    def __init__(self, supabase_client):
        self.db = supabase_client
        self.ist = timezone(timedelta(hours=5, minutes=30))
        self._user_data_cache = {}  # user_id, server_id -> (points, acumen_level)

    async def get_user_data(self, user_id: int, server_id: int):
        cache_key = (user_id, server_id)
        if cache_key in self._user_data_cache:
                    return self._user_data_cache[cache_key]
                
        result = await self.db.from_("usersanagrams").select("points, acumen_level").eq("user_id", user_id).eq("server_id", server_id).execute()
        if result.data:
            user_data = result.data[0]
            points, acumen = user_data['points'], user_data['acumen_level']
        else:
            new_user_data = {
                "user_id": user_id,
                "server_id": server_id,
                "points": 0,
                "acumen_level": 50
            }
            await self.db.from_("usersanagrams").insert(new_user_data).execute()
            points, acumen = 0, 50
        self._user_data_cache[cache_key] = (points, acumen)
        return points, acumen
    async def update_user_data(self, user_id: int, server_id: int, new_points: int, new_acumen_level: int):
        cache_key = (user_id, server_id)
        
        update_data = {
            "points": new_points,
            "acumen_level": int(new_acumen_level)
        }
        if cache_key in self._user_data_cache:
            self._user_data_cache[cache_key] = (new_points, new_acumen_level)

        await self.db.from_("usersanagrams").update(update_data).eq("user_id", user_id).eq("server_id", server_id).execute()

    async def update_user_data_pts(self, user_id: int, server_id: int, new_points: int):
        cache_key = (user_id, server_id)
        
        update_data = {
            "points": new_points,
        }
        if cache_key in self._user_data_cache:
            cached_points, cached_acumen = self._user_data_cache[cache_key]
            self._user_data_cache[cache_key] = (new_points, cached_acumen)
            
        await self.db.from_("usersanagrams").update(update_data).eq("user_id", user_id).eq("server_id", server_id).execute()

    async def get_leaderboard(self, server_id:int):
        result = await self.db.from_("usersanagrams").select("user_id, points, acumen_level").eq("server_id", server_id).order("points", desc=True).limit(10).execute()       
        return [
            {
                "user_id": entry['user_id'],
                "points": entry['points'],
                "acumen": entry['acumen_level'],
                "server_rank": idx + 1
            }
            for idx, entry in enumerate(result.data)
        ]


class AcumenQueue:
    """maintain a queue of active users performances to decide difficulty of next word"""
    def __init__(self, max_size=20):
        self.queue = []
        self.max_size = max_size
        
    def add_user_message(self, user_id: int, acumen: int, message_time: datetime):
        current_time = datetime.now(timezone.utc)
        self.queue = [entry for entry in self.queue 
                     if (current_time - entry['time']).total_seconds() <= 3600]
        
        self.queue.append({
            'user_id': user_id,
            'acumen': acumen,
            'time': message_time
        })
        
        if len(self.queue) > self.max_size:
            self.queue.pop(0)
            
    def get_dynamic_acumen(self) -> int:
        if not self.queue:
            return 20  # Default lowest acumen level
        
        # pick random acumen of active users
        acumen_levels = [entry['acumen'] for entry in self.queue]
        avg_acumen_rand = random.choice(acumen_levels)

        # occasionally bounce up and down in irl
        if random.random() < 0.3:
            return max(20, avg_acumen_rand - 30)
        elif random.random() < 0.4:
            return min(100, avg_acumen_rand + 30)
        
        return avg_acumen_rand
    
class AnagramGame:
    def __init__(self, db_handler):
        self.db_handler = db_handler
        self.game_state = defaultdict(dict)  # server_id -> game state
        self.consecutive_misses = defaultdict(int)  # server_id -> miss count
        self.cooldown_times = defaultdict(lambda: 100)  # server_id -> cooldown time
        self.powerups = defaultdict(int)  # (user_id, server_id) -> remaining powerup uses
        self.streaks = defaultdict(lambda: [0, 0])  # (server_id) -> [user_id, current streak]
        self.acumen_queues = defaultdict(AcumenQueue)
        self.recent_answers = defaultdict(list) # server_id -> [(user_id, time)]
        LEVEL_BOUNDARIES = [0, 1025, 5924, 14915, 19100] # inferred based on score plot

        # precomputed scores from 20k filtered SFW words from wiktionary and Barron GRE. 
        # computed from components of crpytanalysis letter frequency, scrabble score, brute-force combinations, length of word, and frequency of encountering such word on internet.
        with open('word_score_gloss_sorted.csv', 'r') as file:
            reader = csv.DictReader(file)
            self.words_levels = {
                1: [],
                2: [],
                3: [],
                4: [],
                5: []
            }
            for idx, row in enumerate(reader):
                word_info = (row['Word'], int(row['Score']), row['Gloss'])
                if idx < LEVEL_BOUNDARIES[1]:
                    self.words_levels[1].append(word_info)
                elif idx < LEVEL_BOUNDARIES[2]:
                    self.words_levels[2].append(word_info)
                elif idx < LEVEL_BOUNDARIES[3]:
                    self.words_levels[3].append(word_info)
                elif idx < LEVEL_BOUNDARIES[4]:
                    self.words_levels[4].append(word_info)
                else:
                    self.words_levels[5].append(word_info)


    def get_user_key(self, user_id: int, server_id: int) -> tuple:
        return (user_id, server_id)
    
    def word_shuffle(self, word: str) -> str:
        letters = list(word)
        while ''.join(letters) == word or ''.join(letters) in other_possible_words: #make sure we dont reveal answer :P
            random.shuffle(letters)

        return ''.join(letters)

    def generate_hints(self, word: str, anagram: str) -> Tuple[str, str]:
        # keep one letter in correct position
        first_hint = list(anagram)
        first_hint.remove(word[0])
        first_hint.insert(0, f"**{word[0]}**")
        first_hint = ''.join(first_hint)

        second_hint = list(first_hint)
        second_hint.remove(word[-1])
        second_hint.append(f"**{word[-1]}**")
        second_hint = ''.join(second_hint)
        return first_hint, second_hint
    
    async def generate_anagram(self, server_id: int):
        acumen_level = self.acumen_queues[server_id].get_dynamic_acumen()
        word_level = min(5, max(1, int(acumen_level/20)))
        # pick new word dynamically based on acumen
        word_info = random.choice(self.words_levels[word_level])
        word, base_points, definition = word_info
        anagram = self.word_shuffle(word)
        is_bomb = random.randint(1, 100) == 1
        first_hint, second_hint = self.generate_hints(word, anagram)
        if word_level == 5:
            second_hint = definition # for hardest words,the hint is definitions
        game_state = {
            "word": word,
            "anagram": anagram,
            "base_points": base_points, 
            "first_hint": first_hint,
            "second_hint": second_hint,
            "def": definition,
            "is_bomb": is_bomb,
            "start_time": datetime.now(),
            "hint1_sent": False,
            "hint2_sent": False, 
            "cooldown_adjusted": False,
            "other_answers": {}
        }
        self.game_state[server_id] = game_state
        return game_state
    
    def check_hints(self, guess, word, server_id):
        if guess in other_possible_words and guess not in self.game_state[server_id]["other_answers"]:
            self.game_state[server_id]["other_answers"].add(guess)
            return True, "You got 20 points for finding anagram but not exact answer. Think again"
        distance, edit_letter = simplified_levenshtein(guess, word)
        if distance >2 : 
            return False, None
        elif edit_letter:
            return False, edit_letter # to react easily for missing letter typos
        return False, "Please check typos"

    async def check_guess(self, user_id: int, server_id: int, guess: str, guess_time: float):
        user_key = self.get_user_key(user_id, server_id)
        game_state = self.game_state[server_id]
        if not game_state: return
        has_capital = guess[0].isupper()
        guess = guess.lower()
        correct = game_state["word"] == guess
        user_having_streak = self.streaks[server_id]
        daily_multiplier = 1
        
        if correct:
            cached_recent_answers = self.recent_answers[server_id]
            if cached_recent_answers and guess_time - cached_recent_answers[0][1] > 1.2:
                cached_recent_answers = [
                    ans for ans in cached_recent_answers
                    if guess_time - ans[1] <= 1.2
                ]
                self.recent_answers[server_id] = cached_recent_answers

            multiplier_answer_not_first = 1
            self.recent_answers[server_id].append((user_id, guess_time))
            # handle users whose network maybe slow and users who could be on mobile (dont cheat) with exact timestamps
            if len(self.recent_answers[server_id]) == 1:
                multiplier_answer_not_first = 1
            elif has_capital and guess_time - self.recent_answers[server_id][0][1] <= 1.2:
                multiplier_answer_not_first = 0.5
            elif guess_time - self.recent_answers[server_id][0][1] <= 1.0:
                multiplier_answer_not_first =  0.5
            else:
                return
            
        if not correct:
            partial_correct, hint = self.check_hints(guess, game_state["word"], server_id)
            if partial_correct:
                points, acumen = await self.db_handler.get_user_data(user_id, server_id)
                points += 20
                await self.db_handler.update_user_data_pts(user_id, server_id, points)
                return 20, hint
            elif hint:
                return 0, hint 
            else:
                return 0, None
            
        else:
            points, acumen = await self.db_handler.get_user_data(user_id, server_id)

            streak = 1
            if multiplier_answer_not_first == 1 and user_having_streak[0] == user_id:
                user_having_streak[1] += 1
                streak = user_having_streak[1]
            elif multiplier_answer_not_first == 1 or not user_having_streak[0]:
                self.streaks[server_id] = [user_id, 1]
                        
            base_points = game_state["base_points"] 
        
            if self.powerups[user_key] > 0:
                daily_multiplier = 2
                self.powerups[user_key] -= 1
            streak_bonus = 42 * (streak // 5) if streak % 5 == 0 else (5 if streak > 5 else 0)

            word_points = base_points + streak_bonus 
            turn_points = word_points * daily_multiplier * multiplier_answer_not_first
            points += turn_points
            start_time = game_state["start_time"].timestamp()
            elapsed_time = start_time - guess_time

            new_acumen =  max(1, min(100, int(acumen + 11 - (acumen / 10)- 30 * (1 - math.exp(-0.025 * elapsed_time)))))

            self.acumen_queues[server_id].add_user_message(user_id, new_acumen, datetime.fromtimestamp(guess_time, tz=timezone.utc))
            return turn_points, points, streak_bonus, True, new_acumen
        
        return None
    
    async def use_powerup(self, user_id: int, server_id: int):
        user_key = self.get_user_key(user_id, server_id)
        result = await self.db_handler.db.from_("usersanagrams").select("last_powerup").eq("user_id", user_id).eq("server_id", server_id).execute()
        if result.data:
            last_powerup = result.data[0].get('last_powerup')
        else:
            return "Please play first! 😒"
        now_ist = datetime.now(self.db_handler.ist)
        if last_powerup and (now_ist.date() == datetime.fromisoformat(last_powerup).astimezone(self.db_handler.ist).date()):
            return "STML? You have already used powerup today!"
        else:
            last_powerup_timestamp = now_ist.isoformat()
            self.powerups[user_key] = 3
            await self.db_handler.db.from_("usersanagrams").update({"last_powerup": last_powerup_timestamp}).eq("user_id", user_id).eq("server_id", server_id).execute()
        return "2x powerup for next three turns! all the best!"

class CooldownManager:
    def __init__(self):
        self.base_cooldown = 180
        self.min_cooldown = 15
        self.max_cooldown = 900
        self.cooldowns = defaultdict(lambda: self.base_cooldown)
        self.miss_counts = defaultdict(int)
        
    def adjust_cooldown(self, server_id: int, correct: bool):
        if self.cooldowns[server_id] == 900:
            self.miss_counts[server_id] = 2
            self.cooldowns[server_id] = self.base_cooldown
        if correct:
            # Exponential decrease for correct answers
            self.cooldowns[server_id] = max(
                self.min_cooldown,
                self.cooldowns[server_id] //2
            )
            self.miss_counts[server_id] = 0
        else:
            self.miss_counts[server_id] += 1
            if self.miss_counts[server_id] > 3:
                # Sleep mode
                self.cooldowns[server_id] = self.max_cooldown
            else:
                # Linear increase for incorrect answers
                self.cooldowns[server_id] = int(min(
                    self.max_cooldown,
                    self.cooldowns[server_id] * 1.2
                ))
        return self.cooldowns[server_id]
