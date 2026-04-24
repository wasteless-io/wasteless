
import boto3
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timedelta
from dotenv import load_dotenv

def list_running_instances(self):
    """Liste toutes les instances EC2 running"""
    ec2 = boto3.client('ec2',
        region_name=os.getenv('AWS_REGION'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )
    
    response = ec2.describe_instances(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    )
    
    instances = []
    for reservation in response['Reservations']:
        for instance in reservation['Instances']:
            name = ''
            if 'Tags' in instance:
                for tag in instance['Tags']:
                    if tag['Key'] == 'Name':
                        name = tag['Value']
            
            instances.append({
                'instance_id': instance['InstanceId'],
                'instance_type': instance['InstanceType'],
                'instance_name': name,
                'state': instance['State']['Name'],
                'launch_time': instance['LaunchTime']
            })
    
    return instances