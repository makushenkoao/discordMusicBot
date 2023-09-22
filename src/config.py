import os
from dotenv import load_dotenv


load_dotenv()


class BaseConfig:
    TOKEN = os.getenv('TOKEN')
    FFMPEG_PATH = os.getenv('FFMPEG_PATH', 'ffmpeg')
