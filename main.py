from anagram_bot import AnagramGame, CooldownManager, AnagramDatabaseHandler, AcumenQueue

async def db_init():
    db = await create_supabase(supabase_url, supabase_key,
        options=ClientOptions(
            postgrest_client_timeout=10,
            storage_client_timeout=10,
            schema="public",
        ))
    db_handler = DatabaseHandler(db)
    anagram_game_db_handler = AnagramDatabaseHandler(db)
    return db_handler, anagram_game_db_handler
db_handler = None

@bot.event
async def on_ready() -> None:
    """
    starting function after bot restarts
    """
    global db_handler
    global anagram_game_db_handler
    global anagram_state_handler
    db_handler, anagram_game_db_handler = await db_init()
    anagram_state_handler = anagram_state(anagram_game_db_handler)
    await anagram_state_handler.initialize_games(anagram_state_handler.allowed_channels)
    await anagram_state_handler.anagram_loop.start()

cooldown_state_handler = CooldownManager()
class anagram_state:
    def __init__(self, anagram_game_db_handler):
        self.db_handler = anagram_game_db_handler
        self.game = AnagramGame(anagram_game_db_handler)
        self.allowed_channels = {
            # server_id: channel_id
        }
        self.channels = dict()
    async def initialize_games(self, allowed_channels):
        try:
            for server_id, channel_id in allowed_channels.items():
                channel = bot.get_channel(channel_id)
                self.channels.update({server_id: channel})
                if channel:
                    new_game = await self.game.generate_anagram(server_id)
                    await channel.send(f"Starting a new game! Anagram: {new_game['anagram']}" +
                                    (" 💣" if new_game["is_bomb"] else ""))
        except:
            pass

    @tasks.loop(seconds=2)
    async def anagram_loop(self):
        """Main game loop that handles timing and hints."""
        for server_id, game_state in list(self.game.game_state.items()):
                if not game_state:
                    continue
                try:
                    if not await self.game.acquire_lock(server_id):
                        continue
                except Exception as E: 
                    logger.error(E)
                    print("lock error failed")
                channel = self.channels[server_id]
                if not game_state or not game_state.get("start_time", None):
                    continue
                elapsed_time = (datetime.now() - game_state["start_time"]).total_seconds()
                
                max_time = 30 if game_state["is_bomb"] else 240
                current_cooldown = cooldown_state_handler.cooldowns[server_id]
                if current_cooldown > 240:
                    max_time = current_cooldown - 60
                
                try:
                    if elapsed_time >= max_time:
                        if not game_state: continue
                        time_to_sleep = await cooldown_state_handler.adjust_cooldown(server_id, False)
                        self.game.cooldown_times[server_id] = time_to_sleep
                        time_to_sleep = 60 if time_to_sleep == 900 else time_to_sleep
                        self.game.streaks[server_id] = [0, 0]
                        self.game.state_locks[server_id].release()
                        await self.game.transition_to_new_game(server_id, channel, time_to_sleep, timeout = True)
                        continue
                    if not game_state["hint1_sent"] and elapsed_time >= (15 if game_state["is_bomb"] else 30):
                        if self.game.state_locks[server_id].locked():
                            self.game.state_locks[server_id].release()
                        await self.game.send_hint(server_id, channel, 1)
                    if game_state and not game_state["hint2_sent"] and not game_state["is_bomb"] and elapsed_time >= 120:
                        if self.game.state_locks[server_id].locked():
                            self.game.state_locks[server_id].release()
                        await self.game.send_hint(server_id, channel, 2)
                        
                except:
                    self.channels[server_id] = bot.get_channel(self.allowed_channels[server_id])
                if self.game.state_locks[server_id].locked():
                    self.game.state_locks[server_id].release()
                    
                    
@bot.event
async def on_message(message: discord.Message) -> None:
    """triggered for every msg"""
        
    global anagram_state_handler
    if anagram_state_handler:
        anagram_channels = anagram_state_handler.allowed_channels
    
        if message.channel.id == anagram_channels.get(message.guild.id, None):
            game = anagram_state_handler.game
            game_state = game.game_state[message.guild.id]
            guess_time = message.created_at.timestamp()

            if msg.startswith(';'):
                if msg == ';top':
                    lb = await anagram_game_db_handler.get_leaderboard(message.guild.id)
                    try:
                        embed = discord.Embed(title="Leaderboard top 10", description="be careful of these in server:", color=discord.Color.blue())
                        for user in lb:
                            embed.add_field(name = '', value=f"{user['server_rank']}. <@{user['user_id']}>: {user['points']} pts / {user['acumen']}s speed avg.", inline=False)
                        await message.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                        return
                    except Exception as e:
                        logger.error(f"Error getting leaderboard {e}")
                        return
                if msg == ';daily':
                    response = await game.use_powerup(message.author.id, message.guild.id)
                    await message.reply(response)
                    return
            elif len(msg.strip().split())==1:
                if not game_state: 
                    return         
                word = ''.join(filter(str.isalpha, msg))
                answer_check = await game.check_guess(message.author.id, message.guild.id, word, guess_time)

                if not answer_check: 
                    return

                if len(answer_check) == 2:
                    pts, hint = answer_check
                    if pts:
                        await message.reply(f"{hint}", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                        return
                    elif hint and len(hint) == 1:
                        emoji_map = {
                            'a': '\U0001f1e6', 'b': '\U0001f1e7', 'c': '\U0001f1e8', 'd': '\U0001f1e9',
                            'e': '\U0001f1ea', 'f': '\U0001f1eb', 'g': '\U0001f1ec', 'h': '\U0001f1ed',
                            'i': '\U0001f1ee', 'j': '\U0001f1ef', 'k': '\U0001f1f0', 'l': '\U0001f1f1',
                            'm': '\U0001f1f2', 'n': '\U0001f1f3', 'o': '\U0001f1f4', 'p': '\U0001f1f5',
                            'q': '\U0001f1f6', 'r': '\U0001f1f7', 's': '\U0001f1f8', 't': '\U0001f1f9',
                            'u': '\U0001f1fa', 'v': '\U0001f1fb', 'w': '\U0001f1fc', 'x': '\U0001f1fd',
                            'y': '\U0001f1fe', 'z': '\U0001f1ff'
                        }
                        await message.add_reaction(emoji_map.get(hint, '❓'))
                        return
                    elif hint:
                        await message.reply(f"{hint}", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                        return
                else:
                    if not game_state: 
                        return
                    if not await game.acquire_lock(message.guild.id):
                        print("channel lock error failed")
                        return
                    try:
                        turn_points, total_points, streak_bonus, correct, new_acumen = answer_check
                        turn_points = int(turn_points)
                        if correct:
                            bouquets = turn_points//300+1
                            response = f'{"🎉"*int(bouquets)}! '
                            response += f"You got **{turn_points} points** (total: {total_points} and avg. {new_acumen}s speed )"

                            if streak_bonus:
                                response += f" and a streak bonus: **{streak_bonus} points**!"
                            
                            if not game.game_state[message.guild.id].get("cooldown_adjusted"):
                                time_to_sleep = await cooldown_state_handler.adjust_cooldown(message.guild.id, True)
                                game.cooldown_times[message.guild.id] = time_to_sleep
                                game.game_state[message.guild.id]["cooldown_adjusted"] = True  # Mark as adjusted
                                response += f" Next word in **{time_to_sleep} seconds**!"
                                await message.reply(response, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                                await asyncio.sleep(1.2)
                                if game.state_locks[message.guild.id].locked():
                                    game.state_locks[message.guild.id].release()
                                try:
                                    await anagram_game_db_handler.update_user_data(message.author.id, message.guild.id, total_points, new_acumen)
                                except: print(f"score update db fail @{message.author.id} in {message.guild.id} - {total_points} pts")
                                await game.transition_to_new_game(message.guild.id, message.channel, time_to_sleep, timeout=False)
                            else:
                                time_to_sleep = game.cooldown_times[message.guild.id]
                                response += f" Next word in {time_to_sleep} seconds!"
                                await message.reply(response, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                                if game.state_locks[message.guild.id].locked():
                                    game.state_locks[message.guild.id].release()
                                try:
                                    await anagram_game_db_handler.update_user_data(message.author.id, message.guild.id, total_points, new_acumen)
                                except: print(f"score update db fail @{message.author.id} in {message.guild.id} - {total_points} pts")
                        return
                    except Exception as e:
                        print(e)