
import os, sys, string, time, re
import requests, json, urllib, urllib2, base64
from multiprocessing import Pool, Lock, Queue, Manager


class TwitterSentimentAnalyzer:
		
	def __init__(self):
		self.__get_credentials()

	def search(self,search_term, num_tweets):
		# This collection will hold the Tweets as they are returned from Twitter
		collection = []
		# The search URL and headers
		url = "https://api.twitter.com/1.1/search/tweets.json"
		auth = self.__oauth()
		search_headers = {
			"Authorization" : "Bearer %s" % auth['access_token']
			}
		max_count = 100
		next_results = ''
		# Can't stop, won't stop
		while True:
			print "Search iteration, Tweet collection size: %d" % len(collection)
			count = min(max_count, int(num_tweets)-len(collection))

			# Prepare the GET call
			if next_results:
				get_url = url + next_results
			else:
				parameters = {
					'q' : search_term,
					'count' : count,
					'lang' : 'en'
					} 
				get_url = url + '?' + urllib.urlencode(parameters)

			# Make the GET call to Twitter
			results = requests.get(url=get_url, headers=search_headers)
			response = results.json()

			# Loop over statuses to store the relevant pieces of information
			for status in response['statuses']:
				text = status['text'].encode('utf-8')

				# Filter out retweets
				if status['retweeted'] == True:
					continue
				if text[:3] == 'RT ':
					continue

				tweet = {}
				# Configure the fields you are interested in from the status object
				tweet['text']        = text
				tweet['id']          = status['id']
				tweet['time']        = status['created_at'].encode('utf-8')
				tweet['screen_name'] = status['user']['screen_name'].encode('utf-8')
				
				collection    += [tweet]
			
				if len(collection) >= num_tweets:
					print "Search complete! Found %d tweets" % len(collection)
					return collection

			if 'next_results' in response['search_metadata']:
				next_results = response['search_metadata']['next_results']
			else:
				print "Uh-oh! Twitter has dried up. Only collected %d Tweets (requested %d)" % (len(collection), num_tweets)
				print "Last successful Twitter API call: %s" % get_url
				print "HTTP Status:", results.status_code, results.reason
				return collection
			
	def enrich(self, tweets):
			# Prepare to make multiple asynchronous calls to AlchemyAPI
			tweets = self.__dedup(tweets)
			print "Tweets %d" % len(tweets)
			apikey = self.__creds['apikey']
			pool = Pool(processes = 10)
			mgr = Manager()
			result_queue = mgr.Queue()
			# Send each Tweet to the get_text_sentiment function
			for tweet in tweets:
				#print tweet
				pool.apply_async(get_text_sentiment, (apikey, tweet, result_queue))

			pool.close()
			pool.join()
			collection = []
			while not result_queue.empty():
				collection += [result_queue.get()]
			
			print "Enrichment complete! Enriched %d Tweets" % len(collection)
			total_score = self.__evaluate_sentiment(collection)
			return total_score
			
	def __evaluate_sentiment(self,tweets):			
			pos = 0
			neg = 0
			neu = 0
			for tweet in tweets:
				sentiment = tweet["sentiment"]
				if sentiment == "positive":
					pos += 1
				elif sentiment == "negative":
					neg += 1
				else:
					neu += 1
				
			total = {}
			total["positive"] = pos
			total["negative"] = neg
			total["neutral"] = neu
			
			return total
			
	def __get_credentials(self):
			import credentials
			self.__creds = {}
			self.__creds['consumer_key']    = credentials.twitter_consumer_key
			self.__creds['consumer_secret'] = credentials.twitter_consumer_secret
			self.__creds['apikey']          = credentials.alchemy_apikey

	def __oauth(self):
			print "Requesting bearer token from Twitter API"

			try:
				# Encode credentials
				encoded_credentials = base64.b64encode(self.__creds['consumer_key'] + ':' + self.__creds['consumer_secret'])
				# Prepare URL and HTTP parameters
				post_url = "https://api.twitter.com/oauth2/token"
				parameters = {'grant_type' : 'client_credentials'}
				# Prepare headers
				auth_headers = {
					"Authorization" : "Basic %s" % encoded_credentials,
					"Content-Type"  : "application/x-www-form-urlencoded;charset=UTF-8"
					}

				# Make a POST call
				results = requests.post(url=post_url, data=urllib.urlencode(parameters), headers=auth_headers)
				response = results.json()

				# Store the access_token and token_type for further use
				auth = {}
				auth['access_token'] = response['access_token']
				auth['token_type']   = response['token_type']

				print "Bearer token received"
				return auth

			except Exception as e:
				print "Failed to authenticate with Twitter credentials:", e
				print "Twitter consumer key:", self.__creds['consumer_key']
				print "Twitter consumer secret:", self.__creds['consumer_secret']
				sys.exit()
				
	def __dedup(self,tweets):
			used_ids = []
			collection = []
			for tweet in tweets:
				if tweet['id'] not in used_ids:
					used_ids += [tweet['id']]
					collection += [tweet]
			print "After de-duplication, %d tweets" % len(collection)
			return collection

			
def get_text_sentiment(apikey, tweet, output):

	# Base AlchemyAPI URL for targeted sentiment call
	alchemy_url = "http://access.alchemyapi.com/calls/text/TextGetTextSentiment"
	
	# Parameter list, containing the data to be enriched
	parameters = {
		"apikey" : apikey,
		"text"   : tweet['text'],
		"outputMode" : "json",
		"showSourceText" : 1
		}

	try:
		results = requests.get(url=alchemy_url, params=urllib.urlencode(parameters))
		response = results.json()
	except Exception as e:
		print "Error while calling TextGetTargetedSentiment on Tweet (ID %s)" % tweet['id']
		print "Error:", e
		return

	try:
		if 'OK' != response['status'] or 'docSentiment' not in response:
			print "Problem finding 'docSentiment' in HTTP response from AlchemyAPI"
			print response
			print "HTTP Status:", results.status_code, results.reason
			print "--"
			return

		tweet['sentiment'] = response['docSentiment']['type']
		tweet['score'] = 0.
		if tweet['sentiment'] in ('positive', 'negative'):
			tweet['score'] = float(response['docSentiment']['score'])
		output.put(tweet)

	except Exception as e:
		print "D'oh! There was an error enriching Tweet (ID %s)" % tweet['id']
		print "Error:", e
		print "Request:", results.url
		print "Response:", response

	return
		