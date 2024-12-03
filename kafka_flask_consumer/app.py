import logging
import os
import threading
import json
import time

from flask import Flask, jsonify, render_template, request
from confluent_kafka import Consumer, KafkaException, KafkaError

# Configure logging for detailed output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Create a Flask app instance
app = Flask(__name__)

# Retrieve Kafka broker and topic information from environment variables
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')  # Default Kafka broker URL
TOPIC_NAME = os.getenv('TOPIC_NAME', 'train-sensor-data')  # Default Kafka topic name
VEHICLE_NAME = os.getenv('VEHICLE_NAME', 'e700_4801') # Default vehicle name

# Patterns to match different types of Kafka topics
TOPIC_PATTERNS = {
    "anomalies": "^.*_anomalies$",  # Topics containing anomalies
    "normal_data": '^.*_normal_data$', # Topics with normal data
    "statistics" : '^.*_statistics$' # Topics with statistics data
}

# Validate the required environment variables
if not KAFKA_BROKER:
    raise ValueError("Environment variable KAFKA_BROKER is missing.")
if not TOPIC_NAME:
    raise ValueError("Environment variable TOPIC_NAME is missing.")

# Initialize caches to store incoming messages
message_cache = {
    "simulate" : [], # Cache for simulated messages
    "real" : [], # Cache for real messages
    "anomalies" : [],  # Cache for anomaly messages
    "normal" : [] # Cache for normal messages
}

# Initialize a cache to store vehicle statistics
vehicle_stats_cache={}

# Maximum number of messages to store in the cache
MAX_MESSAGES = 100  # Limit for the number of stored messages

# Kafka consumer configuration
KAFKA_CONSUMER_CONFIG = {
    'bootstrap.servers': KAFKA_BROKER,  # Kafka broker URL
    'group.id': 'kafka-consumer-group-1',  # Consumer group for offset management
    'auto.offset.reset': 'earliest'  # Start reading messages from the beginning if no offset is present
}


def deserialize_message(msg):
    """
    Deserialize a Kafka message from JSON format.

    Args:
        msg (Message): The Kafka message object.

    Returns:
        dict or None: A dictionary containing the deserialized data, or None if deserialization fails.
    """
    try:
        # Decode the message value from bytes to string and parse JSON
        message_value = json.loads(msg.value().decode('utf-8'))
        logging.info(f"Received message from topic {msg.topic()}: {message_value}")
        return message_value
    except json.JSONDecodeError as e:
        logging.error(f"Error deserializing message: {e}")
        return None

def kafka_consumer_thread(topics):
    """
    Background thread to consume messages from Kafka topics.

    Args:
        topics (list): List of topics to subscribe to.
    """
    logging.info(f"Subscribing to topics: {topics}")
    consumer = Consumer(KAFKA_CONSUMER_CONFIG)
    consumer.subscribe(topics)

    retry_delay = 1  # Initial delay in seconds

    try:
        while True:
            msg = consumer.poll(1.0)  # Poll for new messages with a timeout of 1 second
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logging.info(f"End of partition reached: {msg.error()}")
                else:
                    logging.error(f"Consumer error: {msg.error()}")
                continue

            # Deserialize the message and process it
            deserialized_data = deserialize_message(msg)
            if deserialized_data:
                logging.info(f"Processing message from topic {msg.topic()}: {deserialized_data}")
                processing_message(msg.topic(), deserialized_data)
            else:
                logging.warning("Deserialized message is None")

            retry_delay = 1  # Reset retry delay on success
    except Exception as e:
        logging.error(f"Error while reading message: {e}")
        logging.info(f"Retrying in {retry_delay} seconds...")
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)  # Exponential backoff, max 60 seconds
    finally:
        consumer.close()  # Close the Kafka consumer on exit

def processing_message(topic, msg):
    """
    Process a Kafka message based on its topic type.

    Args:
        topic (str): The topic from which the message was received.
        msg (dict): The deserialized message data.
    """
    try:
        if topic.endswith("_anomalies"):
            logging.info(f"ANOMALIES ({topic}) - Deserialized message: {msg}")
            add_to_cache("anomalies", msg)
            add_to_cache("real", msg)
            logging.info(f"DATA ({topic}) - {msg}")
        elif topic.endswith("_normal_data"):
            logging.info(f"NORMAL DATA ({topic}) - Deserialized message: {msg}")
            add_to_cache("normal", msg)
            add_to_cache("real", msg)
            logging.info(f"DATA ({topic}) - {msg}")
        elif len(msg.keys()) == 5:
            logging.info(f"5K ({topic}) - Deserialized message: {msg}")
            add_to_cache("simulate", msg)
            logging.info(f"DATA ({topic}) - {msg}")
        elif topic.endswith("_statistics"):
            logging.info(f"STATISTICS ({topic}) - Deserialized message: {msg}")
            process_stat_message(msg)
        else:
            logging.warning(f"Uncategorized message from topic {topic}: {msg}")
    except Exception as e:
        logging.error(f"Error processing message from topic {topic}: {e}")

def add_to_cache(cache_key, message):
    """
    Add a message to the appropriate cache, maintaining a maximum cache size.

    Args:
        cache_key (str): The cache key to store the message under.
        message (dict): The message to store in the cache.
    """
    message_cache[cache_key].append(message)
    # Limit the cache size to the last MAX_MESSAGES entries
    message_cache[cache_key] = message_cache[cache_key][-MAX_MESSAGES:]

def process_stat_message(msg):
    """
    Update vehicle statistics based on a received statistics message.

    Args:
        msg (dict): The statistics message data.
    """
    try:
        vehicle_name=msg.get("vehicle_name", "unknown_vehicle")
        logging.info(f"Processing statistics for vehicle: {vehicle_name}")

        # Initialize statistics for the vehicle if not already present
        if vehicle_name not in vehicle_stats_cache:
            vehicle_stats_cache[vehicle_name] = {'total_messages': 0, 'anomalies_messages': 0, 'normal_messages': 0}

        # Update statistics for the vehicle
        for key in vehicle_stats_cache[vehicle_name]:
            previous_value = vehicle_stats_cache[vehicle_name][key]
            increment = msg.get(key, 0)
            vehicle_stats_cache[vehicle_name][key] += increment
            logging.info(f"Updated {key} for {vehicle_name}: {previous_value} -> {vehicle_stats_cache[vehicle_name][key]}")
    except Exception as e:
        logging.error(f"Error while processing statistics: {e}")


@app.route('/', methods=['GET'])
def home():
    """
    Render the home page.

    Returns:
        str: The HTML for the home page.
    """
    return render_template('index.html')

@app.route('/my-all-data')
def get_data():
    """
    Render the page displaying the last 100 simulated messages.

    Returns:
        str: The HTML for the simulated data visualization page.
    """
    return render_template('trainsensordatavisualization.html', messages=message_cache["simulate"])

@app.route('/my-all-data-by-type')
def get_data_by_type():
    """
    Render the page displaying simulated messages sorted by type.

    Returns:
        str: The HTML for the sorted simulated data visualization page.
    """
    sorted_data_by_type = sort_data_by_type(message_cache["simulate"])
    return render_template('trainsensordatavisualization.html', messages=sorted_data_by_type)

@app.route('/real-all-data')
def get_all_real_data():
    """
    Render the page displaying the last 100 real messages.

    Returns:
        str: The HTML for the real data visualization page.
    """
    return render_template('realdatavisualization.html', messages=message_cache["real"])

@app.route('/real-anomalies-data')
def get_anomalies_real_data():
    """
    Render the page displaying the last 100 anomaly messages.

    Returns:
        str: The HTML for the anomaly data visualization page.
    """
    return render_template('realdatavisualization.html', messages=message_cache["anomalies"])

@app.route('/real-normal-data')
def get_normal_real_data():
    """
    Render the page displaying the last 100 normal messages.

    Returns:
        str: The HTML for the normal data visualization page.
    """
    return render_template('realdatavisualization.html', messages=message_cache["normal"])

@app.route('/statistics')
def get_statistics():
    """
    Render the statistics page with vehicle statistics.

    Returns:
        str: The HTML for the statistics page.
    """
    sorted_stats = {k: vehicle_stats_cache[k] for k in sorted(vehicle_stats_cache)}
    return render_template('statistics.html', all_stats=sorted_stats)


def order_by(param_name, default_value):
    """
    Retrieve a query parameter value from the request or use a default.

    Args:
        param_name (str): The query parameter name.
        default_value (str): The default value to use if the parameter is not provided.

    Returns:
        str: The query parameter value or the default.
    """
    return request.args.get(param_name, default_value)

def order_by_type():
    """
    Get the order parameter for sensor_type.

    Returns:
        str: The order parameter for sensor_type, defaults to 'sensor_type'.
    """
    return order_by('order_by_type', 'sensor_type')

def sort_data_by_type(data_list):
    """
    Sort sensor data by the specified type.

    Args:
        data_list (list): A list of sensor data dictionaries.

    Returns:
        list: The sorted list of sensor data.
    """
    return sorted(data_list, key=lambda x: x.get(order_by_type()))

# Start a background thread to consume Kafka messages
threading.Thread(target=kafka_consumer_thread, args=([TOPIC_NAME, *TOPIC_PATTERNS.values()], ), daemon=True).start()

# Start the Flask web application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)