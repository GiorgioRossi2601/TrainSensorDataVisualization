import logging
import os
import threading
import json
import kafka
from flask import Flask, jsonify, render_template, request
from confluent_kafka import Consumer, KafkaException, KafkaError
from pyexpat.errors import messages

# Configure logging for detailed output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Create a Flask app instance
app = Flask(__name__)

# Read Kafka configuration from environment variables
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')  # Kafka broker URL
TOPIC_NAME = os.getenv('TOPIC_NAME', 'train-sensor-data')  # Kafka topic name
VEHICLE_NAME=os.getenv('VEHICLE_NAME', 'e700_4801')

topic_anomalies='^.*_anomalies$'
topic_normal_data='^.*_normal_data$'

# Validate that KAFKA_BROKER and TOPIC_NAME are set
if not KAFKA_BROKER:
    raise ValueError("Environment variable KAFKA_BROKER is missing.")
if not TOPIC_NAME:
    raise ValueError("Environment variable TOPIC_NAME is missing.")


# List to store received messages and a constant for the maximum number of stored messages
simulate_msg_list = []  # Stores simulated sensor messages

real_msg_list = []  # Stores real sensor messages
anomalies_msg_list = []
normal_msg_list = []

MAX_MESSAGES = 100  # Limit for the number of stored messages

received_all_real_msg = 0
received_anomalies_msg = 0
received_normal_msg = 0

vehicle_stats_cache={}
# Default structure for all vehicles
default_stats = {'total_messages': 0, 'anomalies_messages': 0, 'normal_messages': 0}


# Kafka consumer configuration
conf_cons = {
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

def kafka_consumer_thread():
    """
    Kafka consumer thread function that reads and processes messages from the Kafka topic.

    This function continuously polls the Kafka topic for new messages, deserializes them,
    and appends them to the global simulate_msg_list or real_msg_list based on the message structure.
    """
    consumer = Consumer(conf_cons)
    consumer.subscribe([TOPIC_NAME, topic_anomalies, topic_normal_data])
    global simulate_msg_list, real_msg_list, anomalies_msg_list, normal_msg_list, received_all_real_msg, received_anomalies_msg, received_normal_msg
    try:
        while True:
            msg = consumer.poll(5.0)  # Poll for messages with a timeout of 1 second
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
                logging.info(f"Received message from topic {msg.topic()}: {deserialized_data}")
                if msg.topic().endswith("_anomalies"):
                    logging.info(f"ANOMALIES ({msg.topic()}) - Deserialized message: {deserialized_data}")
                    anomalies_msg_list.append(deserialized_data)
                    anomalies_msg_list = anomalies_msg_list[-MAX_MESSAGES:]  # Keep only the last MAX_MESSAGES
                    real_msg_list.append(deserialized_data)
                    real_msg_list = real_msg_list[-MAX_MESSAGES:]  # Keep only the last MAX_MESSAGES

                    print(f"DATA ({msg.topic()}) - {deserialized_data}")

                elif msg.topic().endswith("_normal_data"):
                    logging.info(f"NORMAL DATA ({msg.topic()}) - Deserialized message: {deserialized_data}")
                    normal_msg_list.append(deserialized_data)
                    normal_msg_list = normal_msg_list[-MAX_MESSAGES:]  # Keep only the last MAX_MESSAGES
                    real_msg_list.append(deserialized_data)
                    real_msg_list = real_msg_list[-MAX_MESSAGES:]  # Keep only the last MAX_MESSAGES

                    print(f"DATA - {deserialized_data}")

                elif len(deserialized_data.keys()) == 5:
                    # If the message has 5 keys, treat it as simulated data
                    logging.info(f"5K ({msg.topic()}) - Deserialized message: {deserialized_data}")
                    simulate_msg_list.append(deserialized_data)
                    simulate_msg_list = simulate_msg_list[-MAX_MESSAGES:]  # Keep only the last MAX_MESSAGES
                else:
                    # Otherwise, treat it as real sensor data
                    logging.info(f"RK ({msg.topic()}) - Deserialized message: {deserialized_data}")
                    real_msg_list.append(deserialized_data)
                    real_msg_list = real_msg_list[-MAX_MESSAGES:]  # Keep only the last MAX_MESSAGES
            else:
                logging.warning("Deserialized message is None")
    except Exception as e:
        logging.error(f"Error while reading message: {e}")
    finally:
        consumer.close()  # Close the Kafka consumer gracefully

# Start the Kafka consumer thread as a daemon to run in the background
threading.Thread(target=kafka_consumer_thread, daemon=True).start()

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
    return render_template('trainsensordatavisualization.html', messages=simulate_msg_list[-MAX_MESSAGES:])

@app.route('/my-all-data-by-type')
def get_data_by_type():
    """
    Render the data visualization page sorted by sensor type.

    Returns:
        str: The rendered template with sorted simulated message data by type.
    """
    sorted_data_by_type = sort_data_by_type(simulate_msg_list[-100:])
    return render_template('trainsensordatavisualization.html', messages=sorted_data_by_type)

@app.route('/real-all-data')
def get_all_real_data():
    """
    Render the data visualization page for real sensor data.

    Returns:
        str: The rendered template with the last 100 real sensor messages.
    """
    return render_template('realdatavisualization.html', messages=real_msg_list)

@app.route('/real-anomalies-data')
def get_anomalies_real_data():
    """
    Render the data visualization page for real sensor data.

    Returns:
        str: The rendered template with the last 100 real sensor messages.
    """
    return render_template('realdatavisualization.html', messages=anomalies_msg_list)

@app.route('/real-normal-data')
def get_normal_real_data():
    """
    Render the data visualization page for real sensor data.

    Returns:
        str: The rendered template with the last 100 real sensor messages.
    """
    return render_template('realdatavisualization.html', messages=normal_msg_list)

@app.route('/statistics')
def get_statistics():
    # Kafka consumer configuration
    consumer_stat = Consumer({
        'bootstrap.servers': KAFKA_BROKER,  # Kafka broker URL
        'group.id': 'flask-statistics-consumer-group',  # Consumer group ID for message offset tracking
        'auto.offset.reset': 'earliest'  # Start reading from the earliest message if no offset is present
    })
    topic_statistics = '^.*_statistics$'
    consumer_stat.subscribe([topic_statistics])

    try:
        while True:
            msg = consumer_stat.poll(5.0)  # Poll with a timeout of 5 seconds
            if msg is None:
                break  # Exit loop if no messages are received
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logging.info(f"End of partition reached: {msg.error()}")
                else:
                    logging.error(f"Consumer error: {msg.error()}")
                continue
            # Deserialize the message
            deserialized_data = deserialize_message(msg)
            if deserialized_data:
            # Extract the vehicle name from the topic
                vehicle_name = deserialized_data.get("vehicle_name", msg.topic().replace('_statistics', ''))
                # Update or initialize vehicle stats in the cache
                if vehicle_name not in vehicle_stats_cache:
                    vehicle_stats_cache[vehicle_name] = default_stats.copy()

                # Increment statistics in the cache
                for key in default_stats.keys():
                    vehicle_stats_cache[vehicle_name][key] += deserialized_data.get(key, 0)

    except Exception as e:
        logging.error(f"Error while consuming statistics: {e}")
    finally:
        consumer_stat.close()

    all_vehicle_names=sorted(vehicle_stats_cache.keys())
    ordered_stats = {
        vehicle: vehicle_stats_cache[vehicle]
        for vehicle in all_vehicle_names
    }

    return render_template('statistics.html', all_stats=ordered_stats)


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

# Start the Flask web application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)