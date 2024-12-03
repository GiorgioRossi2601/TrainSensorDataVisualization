"""
Kafka Consumer System for Vehicle Data Processing

This script consumes messages from Kafka topics for a specific vehicle, processes them,
and updates statistics. It handles messages related to anomalies and normal data.

Environment Variables:
- KAFKA_BROKER: The URL of the Kafka broker (default: 'kafka:9092').
- VEHICLE_NAME: The name of the vehicle whose data will be processed.

Main Features:
- Consumes messages from <vehicle_name>_anomalies and <vehicle_name>_normal_data topics.
- Publishes statistics to <vehicle_name>_statistics topic.
- Ensures topics exist in Kafka before consuming or producing messages.
- Processes messages in real-time and provides detailed logging.

Usage:
Simply set the required environment variables and run the script.
"""
import argparse
import logging
import os
import threading
import json
import time
from lib2to3.pgen2.parse import Parser

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer, SerializingProducer
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.serialization import StringSerializer


# Configure logging for detailed output
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Read Kafka configuration from environment variables
KAFKA_BROKER = os.getenv('KAFKA_BROKER', 'kafka:9092')  # Kafka broker URL
VEHICLE_NAME=os.getenv('VEHICLE_NAME')

# Validate that KAFKA_BROKER and TOPIC_NAME are set
if not KAFKA_BROKER:
    raise ValueError("Environment variable KAFKA_BROKER is missing.")
if not VEHICLE_NAME:
    raise ValueError("Environment variable VEHICLE_NAME is missing.")

# Log the start of the consumer system for the vehicle
logging.info(f"Starting consumer system for vehicle: {VEHICLE_NAME}")

# List to store received messages
real_msg_list = []  # List to store all real messages
anomalies_msg_list = [] # List to store anomaly messages
normal_msg_list = [] # List to store normal data messages

# Variable to track the count of received messages
received_all_real_msg = 0 # Total number of messages received
received_anomalies_msg = 0 # Number of anomaly messages received
received_normal_msg = 0 # Number of normal data messages received

def update_statistics(received_all_real_msg, received_anomalies_msg, received_normal_msg):
    # Dictionary to store statistics
    stats = {
        'vehicle_name': VEHICLE_NAME,  # Name of the vehicle
        'total_messages': received_all_real_msg,  # Total number of messages
        'anomalies_messages': received_anomalies_msg,  # Total number of anomaly messages
        'normal_messages': received_normal_msg  # Total number of normal data messages
    }
    return stats

# Kafka consumer configuration
def create_consumer():
    """
    Create a Kafka consumer instance configured to consume messages for a specific vehicle.

    Returns:
        Consumer: A Kafka consumer instance configured with the specified broker, group ID, and offset settings.
    """
    conf_cons = {
        'bootstrap.servers': KAFKA_BROKER,  # Kafka broker URL
        'group.id': f'{VEHICLE_NAME}-consumer-group',  # Consumer group ID for message offset tracking
        'auto.offset.reset': 'earliest'  # Start reading from the earliest message if no offset is present
    }
    return Consumer(conf_cons)

# Kafka producer configuration for sending statistics
def create_producer_statistic():
    """
    Create a Kafka producer instance for publishing statistics.

    Returns:
        SerializingProducer: A Kafka producer instance configured to serialize message keys and values as JSON.
    """
    conf_prod_stat={
        'bootstrap.servers': KAFKA_BROKER,  # Kafka broker URL
        'key.serializer': StringSerializer('utf_8'), # Serializer for message keys
        'value.serializer': lambda v, ctx: json.dumps(v) # Serializer for message values
    }
    return SerializingProducer(conf_prod_stat) # Return a new Kafka producer instance

def check_and_create_topics(topic_list):
    """
    Check if the specified Kafka topics exist, and create them if they do not exist.

    Args:
        topic_list (list): A list of topic names to check or create.

    Raises:
        KafkaException: If the topic creation fails for any reason.
    """
    admin_client = AdminClient({'bootstrap.servers': KAFKA_BROKER}) # Admin client for kafka
    existing_topics = admin_client.list_topics(timeout=10).topics.keys() # Get existing topics

    # Identify topics that need to be created
    topics_to_create = [
        NewTopic(topic, num_partitions=1, replication_factor=1)
        for topic in topic_list if topic not in existing_topics
    ]

    # Attempt to create missing topics
    if topics_to_create:
        logging.info(f"Creating missing topics: {[topic.topic for topic in topics_to_create]}")
        result = admin_client.create_topics(topics_to_create)

        # Log success or failure for each topic creation
        for topic, future in result.items():
            try:
                future.result() # Wait for the topic creation to complete
                logging.info(f"Topic '{topic}' created successfully.")
            except KafkaException as e:
                logging.error(f"Failed to create topic '{topic}': {e}")

def produce_statistics(producer):
    """
    Publish the current statistics to the Kafka statistics topic.

    Args:
        producer (Producer): The Kafka producer instance for publishing statistics.
    """
    global received_all_real_msg, received_anomalies_msg, received_normal_msg
    stats = update_statistics(received_all_real_msg,received_anomalies_msg, received_normal_msg)
    topic_statistics=f"{VEHICLE_NAME}_statistics" #  Define the statistics topic
    try:
        # Send statistics to the Kafka topic
        producer.produce(topic=topic_statistics, value=stats) # Publish statistics to Kafka
        producer.flush() # Ensure all messages are sent
        logging.info(f"Statistics published to topic {topic_statistics}: {stats}")
    except Exception as e:
        logging.error(f"Failed to produce statistics: {e}")

def deserialize_message(msg):
    """
    Deserialize the JSON-serialized Kafka message

    Args:
        msg (Message): The Kafka message object.

    Returns:
        dict: The deserialized message as a Python dictionary if successful.
        None: If deserialization fails.

    Logs:
        Logs a warning if deserialization fails.
    """
    try:
        message_value = json.loads(msg.value().decode('utf-8')) # Decode and deserialize the message
        logging.info(f"Received message from topic [{msg.topic()}]")
        return message_value
    except json.JSONDecodeError as e:
        logging.error(f"Error deserializing message: {e}")
        return None

def process_message(topic, msg, producer):
    """
    Process a deserialized Kafka message and update statistics.

    Args:
        topic (str): The topic from which the message was received.
        msg (dict): The deserialized message content.
        producer (Producer): The Kafka producer instance sending updated statistics.
    """
    global received_all_real_msg, received_anomalies_msg, received_normal_msg

    logging.info(f"Processing message from topic [{topic}]")
    if topic.endswith("_anomalies"):
        # Process messages categorized as anomalies
        logging.info(f"ANOMALIES ({topic}) - Processing message")
        anomalies_msg_list.append(msg) # Append to the anomalies list
        received_anomalies_msg += 1 # Increment anomaly message count
    elif topic.endswith("_normal_data"):
        # Process messages categorized as normal data
        logging.info(f"NORMAL DATA ({topic}) - Processing message")
        normal_msg_list.append(msg) # Append to the normal data list
        received_normal_msg += 1 # Increment normal message count

    real_msg_list.append(msg) # Add all processed messages to the real message list
    received_all_real_msg += 1 # Increment total message count
    logging.info(f"DATA ({topic}) - {msg}") # Log the processed message content

    # Send updated statistics
    produce_statistics(producer)


def consume_vehicle_data():
    """
    Consume messages from Kafka topics associated with the vehicle.

    Subscribes to the anomaly and normal data topics for the vehicle, processes messages,
    and updates statistics in real-time.
    """
    topic_anomalies = f"{VEHICLE_NAME}_anomalies" # Topic for anomaly messages
    topic_normal_data = f"{VEHICLE_NAME}_normal_data" # Topic for normal data messages
    topic_statistics= f"{VEHICLE_NAME}statistics" # Topic for statistics

    # Verify and create required topics if they do not exist
    check_and_create_topics([topic_anomalies,topic_normal_data, topic_statistics])

    consumer = create_consumer()  # Initialize Kafka consumer
    producer = create_producer_statistic()  # Initialize Kafka producer for statistics

    consumer.subscribe([topic_anomalies, topic_normal_data]) # Subscribe to relevant topics
    logging.info(f"Started consumer for [{VEHICLE_NAME}] ...")
    logging.info(f"Subscribed to topics: {topic_anomalies}, {topic_normal_data}")

    try:
        while True:
            # Poll messages from Kafka
            msg = consumer.poll(1.0)  # Poll with a 1-second timeout
            if msg is None:
                continue # No message received; continue polling
            if msg.error():
                # Handle consumer errors
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logging.info(f"End of partition reached for {msg.topic()}")
                else:
                    logging.error(f"Consumer error: {msg.error()}")
                continue

            deserialized_data = deserialize_message(msg)  # Deserialize the received message
            if deserialized_data:
                process_message(msg.topic(), deserialized_data, producer) # Process the message
    except KeyboardInterrupt:
        logging.info("Consumer interrupted by user.")
    except Exception as e:
        logging.error(f"Error in consumer for {VEHICLE_NAME}: {e}")
    finally:
        consumer.close() # Ensure the consumer is properly closed
        logging.info(f"Consumer for {VEHICLE_NAME} closed.")

def main():
    """
    Entry point for the Kafka consumer system.

    Initializes and runs a thread to consume messages for the specific vehicle.
    """
    logging.info(f"Setting up consumer for vehicle: {VEHICLE_NAME}")
    thread1=threading.Thread(target=consume_vehicle_data)  # Create a new consumer thread
    thread1.daemon=True # Set the thread as a daemon thread
    logging.info(f"Starting consumer thread for vehicle: {VEHICLE_NAME}")
    thread1.start() # Start the consumer thread
    thread1.join() # Wait for the thread to complete

if __name__=="__main__":
    main() # Run the main function