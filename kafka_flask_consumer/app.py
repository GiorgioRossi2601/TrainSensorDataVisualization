import logging
import os
import threading
import json
from flask import Flask, jsonify, render_template, request
from confluent_kafka import Consumer, KafkaException, KafkaError

# Configure logging for detailed output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Create a Flask app instance
app = Flask(__name__)

# Read Kafka configuration from environment variables
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')  # Kafka broker URL
TOPIC_NAME = os.getenv('TOPIC_NAME', 'train-sensor-data')  # Kafka topic name
VEHICLE_NAME = os.getenv('VEHICLE_NAME', 'e700_4801')

TOPIC_PATTERNS = {
    "anomalies": "^.*_anomalies$",
    "normal_data": '^.*_normal_data$',
    "statistics" : '^.*_statistics$'
}

# Validate that KAFKA_BROKER and TOPIC_NAME are set
if not KAFKA_BROKER:
    raise ValueError("Environment variable KAFKA_BROKER is missing.")
if not TOPIC_NAME:
    raise ValueError("Environment variable TOPIC_NAME is missing.")

message_cache = {
    "simulate" : [],
    "real" : [],
    "anomalies" : [],
    "normal" : []
}

vehicle_stats_cache={}

MAX_MESSAGES = 100  # Limit for the number of stored messages

# Kafka consumer configuration
KAFKA_CONSUMER_CONFIG = {
    'bootstrap.servers': KAFKA_BROKER,  # Kafka broker URL
    'group.id': 'kafka-consumer-group-1',  # Consumer group ID for message offset tracking
    'auto.offset.reset': 'earliest'  # Start reading from the earliest message if no offset is present
}


def deserialize_message(msg):
    """
    Deserialize the JSON-serialized data received from the Kafka Consumer.

    Args:
        msg (Message): The Kafka message object.

    Returns:
        dict or None: The deserialized Python dictionary if successful, otherwise None.
    """
    try:
        # Decode the message and deserialize it into a Python dictionary
        message_value = json.loads(msg.value().decode('utf-8'))
        logging.info(f"Received message from topic {msg.topic()}: {message_value}")
        return message_value
    except json.JSONDecodeError as e:
        logging.error(f"Error deserializing message: {e}")
        return None

def kafka_consumer_thread(topics):
    """
    Kafka consumer thread function that reads and processes messages from the Kafka topic.

    This function continuously polls the Kafka topic for new messages, deserializes them,
    and appends them to the global simulate_msg_list or real_msg_list based on the message structure.
    """
    logging.info(f"Subscribing to topics: {topics}")
    consumer = Consumer(KAFKA_CONSUMER_CONFIG)
    consumer.subscribe(topics)
    #global simulate_msg_list, real_msg_list, anomalies_msg_list, normal_msg_list, received_all_real_msg, received_anomalies_msg, received_normal_msg
    try:
        while True:
            msg = consumer.poll(1.0)  # Poll for messages with a timeout of 1 second
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logging.info(f"End of partition reached: {msg.error()}")
                else:
                    logging.error(f"Consumer error: {msg.error()}")
                continue

            # Deserialize the JSON value of the message
            deserialized_data = deserialize_message(msg)
            if deserialized_data:
                logging.info(f"Processing message from topic {msg.topic()}: {deserialized_data}")
                processing_message(msg.topic(), deserialized_data)
            else:
                logging.warning("Deserialized message is None")
    except Exception as e:
        logging.error(f"Error while reading message: {e}")
    finally:
        consumer.close()  # Close the Kafka consumer gracefully

def processing_message(topic, msg):
    """Processa e smista i messaggi ricevuti dal Kafka Consumer."""
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
    """Aggiunge un messaggio alla cache limitando la lunghezza."""
    message_cache[cache_key].append(message)
    message_cache[cache_key] = message_cache[cache_key][-MAX_MESSAGES:]

def process_stat_message(msg):
    """Processa e smista i messaggi ricevuti dal Kafka Consumer."""
    try:
        #vehicle_name = msg.get("vehicle_name", msg.topic().replace('_statistics', ''))

        vehicle_name=msg.get("vehicle_name", "unknown_vehicle")

        logging.info(f"Processing statistics for vehicle: {vehicle_name}")

        if vehicle_name not in vehicle_stats_cache:
            vehicle_stats_cache[vehicle_name] = {'total_messages': 0, 'anomalies_messages': 0, 'normal_messages': 0}

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
        str: The rendered template for the index page.
    """
    return render_template('index.html')

@app.route('/my-all-data')
def get_data():
    """
    Render the data visualization page with the last 100 messages.

    Returns:
        str: The rendered template with the last 100 simulated messages.
    """
    return render_template('trainsensordatavisualization.html', messages=message_cache["simulate"])

@app.route('/my-all-data-by-type')
def get_data_by_type():
    """
    Render the data visualization page sorted by sensor type.

    Returns:
        str: The rendered template with sorted simulated message data by type.
    """
    sorted_data_by_type = sort_data_by_type(message_cache["simulate"])
    return render_template('trainsensordatavisualization.html', messages=sorted_data_by_type)

@app.route('/real-all-data')
def get_all_real_data():
    """
    Render the data visualization page for real sensor data.

    Returns:
        str: The rendered template with the last 100 real sensor messages.
    """
    return render_template('realdatavisualization.html', messages=message_cache["real"])

@app.route('/real-anomalies-data')
def get_anomalies_real_data():
    """
    Render the data visualization page for real sensor data.

    Returns:
        str: The rendered template with the last 100 real sensor messages.
    """
    return render_template('realdatavisualization.html', messages=message_cache["anomalies"])

@app.route('/real-normal-data')
def get_normal_real_data():
    """
    Render the data visualization page for real sensor data.

    Returns:
        str: The rendered template with the last 100 real sensor messages.
    """
    return render_template('realdatavisualization.html', messages=message_cache["normal"])

@app.route('/statistics')
def get_statistics():
    sorted_stats = {k: vehicle_stats_cache[k] for k in sorted(vehicle_stats_cache)}
    return render_template('statistics.html', all_stats=sorted_stats)


def order_by(param_name, default_value):
    """
    Retrieve the value of a specific request argument or return a default.

    Args:
        param_name (str): The name of the request argument to retrieve.
        default_value (str): The default value to return if the argument is not found.

    Returns:
        str: The value of the argument or the default value.
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
    Sort the given sensor data by type.

    Args:
        data_list (list): The list of sensor data dictionaries to sort.

    Returns:
        list: The sorted list of sensor data by type.
    """
    return sorted(data_list, key=lambda x: x.get(order_by_type()))

# Start the Kafka consumer thread as a daemon to run in the background
threading.Thread(target=kafka_consumer_thread, args=([TOPIC_NAME, *TOPIC_PATTERNS.values()], ), daemon=True).start()

# Start the Flask web application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)