import datetime
import json
import os
import pytz
import re
import time

import bufferapp
from bs4 import BeautifulSoup
import google.oauth2.credentials
import google_auth_oauthlib.flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from twitch import TwitchClient
from itertools import chain, imap


def flatMap(f, items):
    return chain.from_iterable(imap(f, items))


def unix_time_seconds(dt):
    epoch = pytz.UTC.localize(datetime.datetime.utcfromtimestamp(0))
    return (dt - epoch).total_seconds()

# More or less https://github.com/youtube/api-samples/blob/master/python/list_streams.py

# Retrieve a list of the liveStream resources associated with the currently
# authenticated user's channel.
def list_streams(youtube):
  print 'Live streams:'

  list_streams_request = youtube.liveBroadcasts().list(
    part='id,snippet',
    mine=True,
    maxResults=50
  )

  results = []

  # Collect the results over multiple pages of youtube responses
  while list_streams_request:
    list_streams_response = list_streams_request.execute()

    def extract_information(stream):
        parsed_time = datetime.datetime.strptime(
            str(stream['snippet']['scheduledStartTime']),
            '%Y-%m-%dT%H:%M:%S.000Z')
        parsed_time = parsed_time.replace(tzinfo=pytz.UTC)
        timezone = pytz.timezone('US/Pacific')
        parsed_time = parsed_time.astimezone(timezone)
        return {
            "title": stream['snippet']['title'],
            "description": stream['snippet']['description'],
            "id": stream['id'],
            "url": "https://www.youtube.com/watch?v={0}".format(stream['id']),
            "scheduledStartTime": parsed_time,
            "image_url": stream['snippet']['thumbnails']['medium']['url']}
                
    responses = list_streams_response.get('items', [])
    future_streams = filter(
        lambda response: "actualEndTime" not in response["snippet"], responses)
    extracted_values = map(extract_information, future_streams)
    results.extend(extracted_values)
    
    list_streams_request = youtube.liveStreams().list_next(
      list_streams_request, list_streams_response)

  return results


# Authorize the request and store authorization credentials.
def get_authenticated_youtube_service():
    # This OAuth 2.0 access scope allows for read-only access to the authenticated
    # user's account, but not other types of account access.
    SCOPES = ['https://www.googleapis.com/auth/youtube.readonly']
    API_SERVICE_NAME = 'youtube'
    API_VERSION = 'v3'
    CLIENT_SECRETS_FILE = os.getenv("GOOGLE_CLIENT_SECRET")
    AUTH_FILE = os.getenv("G_AUTH_FILE", "g_yt_auth_file")

    def yt_cred_to_dict(credentials):
        """Convert the credentials into a form we can serialize."""
        return  {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'id_token':credentials.id_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes,
        'expiry':datetime.datetime.strftime(credentials.expiry,'%Y-%m-%d %H:%M:%S')
    }

    try:
        with open(AUTH_FILE) as data_file:    
            credentials_dict = json.load(data_file)
            del credentials_dict['expiry']
            credentials = google.oauth2.credentials.Credentials(**credentials_dict)
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)
            if not credentials.valid:
                raise Exception("I'm sad, creds aren't happy")
    except:
        flow = InstalledAppFlow.from_client_secrets_file(
                                CLIENT_SECRETS_FILE,
                                scopes=SCOPES,
                                access_type='offline')
        credentials = flow.run_console()
    with open(AUTH_FILE, 'w') as outfile:
        json.dump(yt_cred_to_dict(credentials), outfile)
    
    return build(API_SERVICE_NAME, API_VERSION, credentials = credentials)


def copy_todays_events():
    # Fetch youtube streams
    youtube = get_authenticated_youtube_service()
    streams = list_streams(youtube)
    # Get a noew in pacific time we can use for scheduling and testing
    # Assumes system time is in pacific or UTC , which holds true on my home computer :p
    now = datetime.datetime.now()
    timezone = pytz.timezone('US/Pacific')
    if "PST" in time.tzname:
        # current timezone is pacific
        now = timezone.localize(now)
    elif "UTC" in time.tznames:
        # current timezone is UTC
        now = pytz.UTC.localize(now)
    else:
        raise Exception("ugh timezones.")
    now = now.astimezone(timezone)

    # Filter to streams in the next 7 days
    def soon(stream):
        delta = stream['scheduledStartTime'] - now
        return delta > datetime.timedelta(minutes = 5) and delta < datetime.timedelta(days = 7)
    
    upcoming_streams = filter(soon, streams)
    for stream in upcoming_streams:
        print(stream)
    
    
    print "Buffer posts:"
    twitch_link = "https://www.twitch.tv/holdenkarau"
    # Update buffer posts
    buffer_clientid = os.getenv("BUFFER_CLIENTID")
    buffer_client_secret = os.getenv("BUFFER_CLIENT_SECRET")
    buffer_token = os.getenv("BUFFER_CODE")
    
    buffer_api = bufferapp.API(
        client_id=buffer_clientid, client_secret=buffer_client_secret,
        access_token=buffer_token)
    user = bufferapp.User(api=buffer_api)
    profiles = bufferapp.Profiles(api=buffer_api).all()

    def format_posts(stream):
        """Create posts for a given stream.
        Returns the short text, long text, and  tuple of schedule time."""
        # Munge the text to fit within our sentence structure
        stream_title = stream['title']
        cleaned_title = stream_title[:1].lower() + stream_title[1:]
        # Cut the text for twitter if needed
        short_title = cleaned_title
        # swap in at mentions on twitter
        short_tile = short_title.replace("Apache Spark", "@ApacheSpark") \
            .replace("Apache Airflow (Incubating)", "@ApacheAirflow") \
            .replace("Apache (Incubating) Airflow", "@ApacheAirflow") \
            .replace("Apache Airflow", "@ApacheAirflow") \
            .replace("Apache Beam", "@ApacheBeam")
        if len(short_title) > 150:
            short_title = cleaned_title[:150] + "..."
        # Compute how far out this event is
        delta = stream['scheduledStartTime'] - now
        yt_link = stream['url']
        def create_join_in_less_than_an_hour(stream):
            tweet_time = stream['scheduledStartTime'] - datetime.timedelta(hours=0, minutes=50)

            def format_time_same_day(time):
                if time.minute == 0:
                    return time.strftime("%-I%p")
                else:
                    return time.strftime("%-I:%M%p")

            stream_time = format_time_same_day(stream['scheduledStartTime'])

            full_text = "Join me in less than an hour @ {0} pacific for {1} on {2} or {3}".format(
                stream_time, cleaned_title, yt_link, twitch_link)
            short_text = "Join me in less than an hour @ {0} pacific for {1} - {2} or {3}".format(
                stream_time, short_title, yt_link, twitch_link)
            return (full_text, short_text, tweet_time)

        def create_join_tomorrow(stream):
            tweet_time = stream['scheduledStartTime'] - datetime.timedelta(hours=23, minutes=55)

            def format_time_tomorrow(time):
                if time.minute == 0:
                    return time.strftime("%a %-I%p")
                else:
                    return time.strftime("%a %-I:%M%p")

            stream_time = format_time_tomorrow(stream['scheduledStartTime'])

            full_text = "Join me tomorrow @ {0} pacific for {1} on {2} or {3}".format(
                stream_time, cleaned_title, yt_link, twitch_link)
            short_text = "Join me tomorrow @ {0} pacific for {1} - {2} or {3}".format(
                stream_time, short_title, yt_link, twitch_link)
            return (full_text, short_text, tweet_time)

        def create_join_me_on_day_x(stream):
            tweet_time = stream['scheduledStartTime'] - datetime.timedelta(days = 4, hours=23, minutes=55)

            def format_time_future(time):
                if time.minute == 0:
                    return time.strftime("%A %-I%p")
                else:
                    return time.strftime("%A %-I:%M%p")

            stream_time = format_time_future(stream['scheduledStartTime'])

            full_text = "Join me this {0} pacific for {1} on {2} or {3}".format(
                stream_time, cleaned_title, yt_link, twitch_link)
            short_text = "Join me this {0} pacific for {1} - {2} or {3}".format(
                stream_time, short_title, yt_link, twitch_link)
            return (full_text, short_text, tweet_time)

        return [create_join_in_less_than_an_hour(stream),
                create_join_me_on_day_x(stream),
                create_join_tomorrow(stream)]

    possible_posts = flatMap(format_posts, streams)

    # Only schedule posts in < 36 hours and < - 12 hours
    def is_reasonable_time(post):
        delta_from_now = post[2] - now
        return delta_from_now < datetime.timedelta(hours=35, minutes=55) and \
            delta_from_now > datetime.timedelta(hours=-12)

    desired_posts = filter(is_reasonable_time, possible_posts)

    def post_as_needed_to_profile(profile):
        print "Profile:"
        print profile
        print "id:"
        print profile.id
        print "type:"
        print profile.formatted_service
        # Special case twitter for short text
        posts = []
        if profile.formatted_service == "Twitter":
            posts = map(lambda post: (post[1], post[2]), desired_posts)
        else:
            posts = map(lambda post: (post[0], post[2]), desired_posts)
        updates = profile.updates
        pending = updates.pending
        sent = updates.sent
        print "Pending"
        print pending
        print "Sent"
        print sent
        all_updates = []
        all_updates.extend(pending)
        all_updates.extend(sent)

        # Get the raw text of the posts to de-duplicate
        def extract_text_from_update(update):
            return BeautifulSoup(
                update.text_formatted,
                features="html.parser").get_text()

        all_update_text = list(map(extract_text_from_update, all_updates))
        print "Updated text:"
        print all_update_text
        unpublished_posts = filter(
            lambda post: post[0] not in all_update_text, posts)
        print "Unpublished posts:"
        print unpublished_posts
        updates = profile.updates
        for post in unpublished_posts:
            # Note: even though we set shorten the backend seems to use the
            # user's per-profile settings instead.
            if post[1] > now:
                target_time_in_utc = post[1].astimezone(pytz.UTC)
                updates.new(post[0], shorten=False,
                            when=unix_time_seconds(target_time_in_utc))
            else:
                updates.new(post[0], shorten=False,
                            now=True)

    for profile in profiles:
        post_as_needed_to_profile(profile)

    def update_twitch():
        """Update twitch. Broken until client lib switches to new API."""
        # Set up twitch posts
        twitch_client = TwitchClient(
            client_id=os.getenv("TWITCH_CLIENT_ID"),
            oauth_token=os.getenv("TWITCH_OAUTH"))
        channel_info = twitch_client.channels.get()
        channel_id = channel_info.id
        print channel_id
        # Get existing updates
        posts = twitch_client.channel_feed.get_posts(
            channel_id=channel_id, comments=None)
        # Ugh this is deprecated now
        # TODO: Wait for twitch client to update to Helix API


if __name__ == '__main__':
    copy_todays_events()