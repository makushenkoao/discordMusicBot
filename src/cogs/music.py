import asyncio
import discord
import validators

from discord import Guild, Member, TextChannel, Interaction, Embed, VoiceState, FFmpegPCMAudio
from discord.ext import commands
from discord.ui import View, Select, Button
from yt_dlp import YoutubeDL
from datetime import datetime, timedelta

from src.config import BaseConfig


class MusicCog(commands.Cog):
    FFMPEG_PATH = BaseConfig.FFMPEG_PATH
    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }
    YDL_OPTIONS = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True
    }
    YOUTUBE_LOGO_URL = 'https://cdn1.iconfinder.com/data/icons/logotypes/32/youtube-512.png'

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.queue = {}
        for guild in self.bot.guilds:
            self.queue[guild.id] = []

        super().__init__()

    async def connect(self, interaction: Interaction) -> None:
        if not self.bot.get_guild(interaction.guild_id).voice_client:
            await interaction.user.voice.channel.connect()

    async def disconnect(self, guild_id: int) -> None:
        if self.bot.get_guild(guild_id).voice_client:
            await self.bot.get_guild(guild_id).voice_client.disconnect(force=True)

    def is_bot_connected(self, guild_id: int) -> bool:
        return self.bot.get_guild(guild_id).voice_client is not None

    def is_user_with_bot(self, interaction: Interaction) -> bool:
        if not self.is_user_connected(interaction):
            return False

        user_channel = interaction.user.voice.channel
        return user_channel == self.bot.get_channel(user_channel.id).guild.voice_client.channel

    def is_first_track(self, guild_id: int) -> bool:
        return len(self.queue[guild_id]) == 1

    def is_queue_empty(self, guild_id: int) -> bool:
        return len(self.queue[guild_id]) == 0

    def update_queue(self, guild_id: int, yt_entry: dict) -> None:
        self.queue[guild_id].append({
            'message': None,
            'source': {
                'url': yt_entry['url'],
                'title': yt_entry['title'],
                'original_url': yt_entry['original_url'],
                'channel': yt_entry['channel'],
                'channel_url': yt_entry['channel_url'],
                'thumbnail': yt_entry['thumbnail'],
                'duration': self.get_formatted_duration(yt_entry.get('duration'))
            }
        })

    async def play_track(self, interaction: Interaction = None,
                         user: Member = None, text_channel: TextChannel = None) -> None:
        if interaction:
            guild_id = interaction.guild_id
            user = interaction.user
            text_channel = interaction.channel
            self.queue[guild_id][0]['message'] = await self.bot.get_channel(text_channel.id).fetch_message(
                (await interaction.followup.send(
                    embed=PlayNowEmbed(self.queue[text_channel.guild.id][0]['source'],
                                       user, self.YOUTUBE_LOGO_URL),
                    view=MusicControlView(self, guild_id)
                )).id
            )
            await self.connect(interaction)
        else:
            guild_id = user.guild.id
            await self.queue[guild_id][0]['message'].edit(view=MusicControlViewDisabled())
            self.queue[guild_id].pop(0)
            if self.is_queue_empty(guild_id):
                await self.disconnect(guild_id)
            else:
                self.queue[guild_id][0]['message'] = await self.bot.get_channel(text_channel.id).send(
                    embed=PlayNowEmbed(self.queue[text_channel.guild.id][0]['source'],
                                       user, self.YOUTUBE_LOGO_URL),
                    view=MusicControlView(self, guild_id)
                )
        if not self.is_queue_empty(guild_id):
            source = FFmpegPCMAudio(
                self.queue[guild_id][0]['source']['url'],
                **self.FFMPEG_OPTIONS,
                executable=self.FFMPEG_PATH
            )
            self.bot.get_guild(guild_id).voice_client.play(
                source,
                after=lambda _: asyncio.run_coroutine_threadsafe(
                    self.play_track(None, user, text_channel),
                    self.bot.loop
                )
            )

    def skip_track(self, interaction: Interaction) -> None:
        self.bot.get_guild(interaction.guild_id).voice_client.stop()

    @staticmethod
    def is_user_connected(interaction: Interaction) -> bool:
        return interaction.user.voice is not None

    @staticmethod
    def get_formatted_duration(duration: int = None) -> str:
        if duration is None:
            return 'Невідомо'

        dt = datetime.utcfromtimestamp(duration)
        if dt.day == 1:
            if dt.hour == 0:
                return dt.strftime('%M:%S')
            return dt.strftime('%H:%M:%S')

        dt -= timedelta(days=1)
        return dt.strftime('%d:%H:%M:%S')

    @staticmethod
    def get_formatted_option(option: str) -> str:
        return option[:90]

    @commands.Cog.listener()
    async def on_guild_join(self, guild: Guild) -> None:
        self.queue[guild.id] = []

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: Guild) -> None:
        self.queue.pop(guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, user: Member, before: VoiceState, after: VoiceState) -> None:
        if user == self.bot.user:
            if not after.channel:
                if self.bot.get_guild(before.channel.guild.id).voice_client:
                    self.bot.get_guild(before.channel.guild.id).voice_client.cleanup()
                if self.queue[before.channel.guild.id]:
                    await self.queue[before.channel.guild.id][0]['message'].edit(view=MusicControlViewDisabled())
                self.queue[before.channel.guild.id] = []
        elif before.channel and self.bot.get_guild(before.channel.guild.id).voice_client:
            if before.channel == self.bot.get_guild(before.channel.guild.id).voice_client.channel:
                if len(before.channel.members) == 1 and before.channel.members[0] == self.bot.user:
                    await self.disconnect(before.channel.guild.id)

    @discord.app_commands.command(name='play', description='Запустити музику з YouTube')
    @discord.app_commands.describe(search='Введіть рядок для пошуку або URL')
    async def command_play(self, interaction: Interaction, search: str) -> None:
        if not self.is_user_connected(interaction):
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send('❌ Ви не підключені до голосового каналу')
            return
        if not self.is_queue_empty(interaction.guild_id) and not self.is_user_with_bot(interaction):
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send('❌ Ви не підключені до голосового каналу, в якому знаходиться бот')
            return

        with YoutubeDL(self.YDL_OPTIONS) as ydl:
            if validators.url(search):
                await interaction.response.defer()

                entry = ydl.extract_info(search, download=False)
                self.update_queue(interaction.guild_id, entry)
                if self.is_first_track(interaction.guild_id):
                    await self.play_track(interaction)
                else:
                    await interaction.followup.send(embed=PlayQueueEmbed(
                        self.queue[interaction.guild_id][-1]['source'],
                        interaction.user,
                        self.YOUTUBE_LOGO_URL
                    ))
            else:
                await interaction.response.defer(ephemeral=True)

                entries = ydl.extract_info(f'ytsearch5:{search}', download=False)['entries']
                if entries:
                    await interaction.followup.send(
                        embed=SearchEmbed(self, entries),
                        view=MusicSelectView(self, interaction, entries)
                    )
                else:
                    await interaction.followup.send('❌ На ваш запит нічого не знайдено')

    @discord.app_commands.command(name='skip', description='Пропустити поточний трек')
    async def command_skip(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self.is_bot_connected(interaction.guild_id):
            await interaction.followup.send('❌ Бот не підключено до голосового каналу')
            return
        if not self.is_user_connected(interaction):
            await interaction.followup.send('❌ Ви не підключені до голосового каналу')
            return
        if not self.is_user_with_bot(interaction):
            await interaction.followup.send('❌ Ви не підключені до голосового каналу, в якому знаходиться бот')
            return

        self.skip_track(interaction)
        if self.is_first_track(interaction.guild_id):
            await interaction.followup.send('👋')
        else:
            await interaction.followup.send('✅ Пропускаю поточний трек')

    @discord.app_commands.command(name='queue', description='Переглянути чергу треків')
    async def command_queue(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self.is_bot_connected(interaction.guild_id):
            await interaction.followup.send('❌ Бот не підключено до голосового каналу')
            return

        await interaction.followup.send(embed=QueueEmbed(
            self.queue,
            interaction.guild_id
        ), ephemeral=True)

    @discord.app_commands.command(name='stop', description='Вимкнути бота')
    async def command_stop(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not self.is_bot_connected(interaction.guild_id):
            await interaction.followup.send('❌ Бот не підключено до голосового каналу')
            return
        if not self.is_user_connected(interaction):
            await interaction.followup.send('❌ Ви не підключені до голосового каналу')
            return
        if not self.is_user_with_bot(interaction):
            await interaction.followup.send('❌ Ви не підключені до голосового каналу, в якому знаходиться бот')
            return

        await self.disconnect(interaction.guild.id)
        await interaction.followup.send('👋')


class MusicSelectView(View):
    def __init__(self, cog: MusicCog, interaction: Interaction, entries: dict = None):
        self.__interaction = interaction

        super().__init__(timeout=60)

        self.add_item(MusicSelect(cog, entries))

    async def on_timeout(self) -> None:
        if self.__interaction:
            await self.__interaction.edit_original_response(view=MusicSelectViewDisabled())


class MusicSelect(Select):
    def __init__(self, cog: MusicCog, entries: dict):
        self.__cog = cog
        self.__entries = entries

        super().__init__(placeholder='Виберіть потрібне', options=[
            discord.SelectOption(label='{}. {}'.format(
                i + 1,
                MusicCog.get_formatted_option(f'{entry["channel"]} - {entry["title"]}')), value=str(i)
            )
            for i, entry in enumerate(self.__entries)
        ])

    async def callback(self, interaction: Interaction) -> None:
        await interaction.response.edit_message(view=MusicSelectViewDisabled())

        if self.__cog.is_user_connected(interaction):
            self.__cog.update_queue(interaction.guild_id, self.__entries[int(self.values[0])])
            if self.__cog.is_first_track(interaction.guild_id):
                await self.__cog.play_track(interaction)
            else:
                if self.__cog.is_user_with_bot(interaction):
                    await self.__cog.bot.get_channel(interaction.channel.id).send(embed=PlayQueueEmbed(
                        self.__cog.queue[interaction.guild_id][-1]['source'],
                        interaction.user,
                        self.__cog.YOUTUBE_LOGO_URL
                    ))


class MusicSelectViewDisabled(View):
    def __init__(self):
        super().__init__()

        self.add_item(Select(options=[discord.SelectOption(label='...')], disabled=True))


class MusicControlView(View):
    def __init__(self, cog: MusicCog, guild_id: int):
        self.__cog = cog
        self.__guild_id = guild_id

        super().__init__(timeout=600)

    @discord.ui.button(emoji='⏭', label='Пропустити', style=discord.ButtonStyle.gray)
    async def btn_skip(self, interaction: Interaction, button: Button):
        await interaction.response.defer()

        if self.__cog.is_user_with_bot(interaction):
            self.__cog.bot.get_guild(interaction.guild_id).voice_client.stop()

    @discord.ui.button(emoji='💬', label='Черга', style=discord.ButtonStyle.gray)
    async def btn_queue(self, interaction: Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)

        if self.__cog.is_user_with_bot(interaction):
            await interaction.followup.send(embed=QueueEmbed(
                self.__cog.queue,
                interaction.guild_id
            ), ephemeral=True)

    @discord.ui.button(label='\u200b', style=discord.ButtonStyle.gray, disabled=True)
    async def btn_stub(self, interaction: Interaction, button: Button):
        await interaction.response.defer()

    @discord.ui.button(label='Вимкнути', style=discord.ButtonStyle.danger)
    async def btn_disconnect(self, interaction: Interaction, button: Button):
        await interaction.response.defer()

        if self.__cog.is_user_with_bot(interaction):
            await self.__cog.disconnect(interaction.guild_id)

    async def on_timeout(self) -> None:
        if self.__cog.is_queue_empty(self.__guild_id):
            return

        self.__cog.queue[self.__guild_id][0]['message'] = await self.__cog.queue[self.__guild_id][0]['message'].edit(
            view=MusicControlView(self.__cog, self.__guild_id)
        )


class MusicControlViewDisabled(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(emoji='⏭', label='Пропустити', style=discord.ButtonStyle.gray, disabled=True)
    async def btn_skip(self, interaction: Interaction, button: Button):
        pass

    @discord.ui.button(emoji='💬', label='Черга', style=discord.ButtonStyle.gray, disabled=True)
    async def btn_queue(self, interaction: Interaction, button: Button):
        pass

    @discord.ui.button(label='\u200b', style=discord.ButtonStyle.gray, disabled=True)
    async def btn_stub(self, interaction: Interaction, button: Button):
        pass

    @discord.ui.button(label='Вимкнути', style=discord.ButtonStyle.danger, disabled=True)
    async def btn_disconnect(self, interaction: Interaction, button: Button):
        pass


class SearchEmbed(Embed):
    def __init__(self, cog: MusicCog, yt_entries: dict):
        self.__cog = cog

        super().__init__(title='Результати пошуку :')

        for i, entry in enumerate(yt_entries, 1):
            self.add_field(
                name='\u200b',
                value='**{}.** {} - [{}]({}) ({})'.format(
                    i,
                    entry['channel'],
                    entry['title'],
                    entry['original_url'],
                    self.__cog.get_formatted_duration(entry.get('duration'))
                ),
                inline=False
            )
        self.set_footer(
            text='YouTube',
            icon_url=self.__cog.YOUTUBE_LOGO_URL
        )


class QueueEmbed(Embed):
    def __init__(self, queue: dict, guild_id: int):
        super().__init__(title='Черга')

        if queue[guild_id][1:]:
            for i, data in enumerate(queue[guild_id][1:], 1):
                self.add_field(
                    name='**{}.** {}'.format(i, data['source']['channel']),
                    value='[{}]({}) ({})'.format(
                        data['source']['title'],
                        data['source']['original_url'],
                        data['source']['duration']
                    ),
                    inline=False
                )
        else:
            self.add_field(
                name='У черзі нічого немає.',
                value='Додайте треки, використовуючи команду **/play**'
            )
        self.set_footer(
            text='YouTube',
            icon_url='https://cdn1.iconfinder.com/data/icons/logotypes/32/youtube-512.png'
        )


class TrackEmbed(Embed):
    def __init__(self, source: dict, user: Member, icon_url: str):
        super().__init__(title=source['title'], url=source['original_url'])

        self.add_field(name='Запрошено користувачем :', value=f'`{user}`', inline=True)
        self.add_field(name='Тривалість :', value='`{}`'.format(source['duration']), inline=True)
        self.set_footer(text='YouTube', icon_url=icon_url)


class PlayNowEmbed(TrackEmbed):
    def __init__(self, source: dict, user: Member, icon_url: str):
        super().__init__(source, user, icon_url)

        self.colour = 15548997
        self.set_author(name=source['channel'], url=source['channel_url'])
        self.set_image(url=source['thumbnail'])


class PlayQueueEmbed(TrackEmbed):
    def __init__(self, source: dict, user: Member, icon_url: str):
        super().__init__(source, user, icon_url)

        self.set_author(name='Трек доданий до черги')
        self.set_thumbnail(url=source['thumbnail'])
