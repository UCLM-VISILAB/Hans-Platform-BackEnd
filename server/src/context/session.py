import json
from datetime import datetime
from enum import Enum
from io import TextIOBase
import time, zipfile, os
from typing import Callable, Dict, Optional, Union
import src.context as ctx
from .mqtt_utils import MQTTClient
from .participant import Participant
from .position_format_utils import convert_trajectory_files
import re

class SessionCommunicator(MQTTClient):
    class Status(Enum):
        DISCONNECTED = 'disconnected'
        CONNECTED = 'connected'
        SUBSCRIBED = 'subscribed'

    def __init__(self, session_id: int, host='localhost', port=1883):
        self.session_id: int = session_id
        self._status = SessionCommunicator.Status.DISCONNECTED

        self.on_status_changed: Callable[[SessionCommunicator.Status], None] = None
        self.on_participant_ready: Callable[[str,int], None] = None
        self.on_participant_leave: Callable[[int,int], None] = None
        self.on_session_start: Callable[[None]] = None
        self.on_session_stop: Callable[[None]] = None
        self.on_setup_question: Callable[[str,int]] = None
        self.on_participant_update: Callable[[int, dict]] = None

        MQTTClient.__init__(self, host, port)
        self.client.message_callback_add('swarm/session/+/control/+', self.control_message_handler)
        self.client.message_callback_add('swarm/session/+/control', self.control_admin_message_handler)
        self.client.message_callback_add('swarm/session/+/updates/+', self.updates_message_handler)

    @property
    def status(self) -> Status:
        return self._status

    @status.setter
    def status(self, status: Status):
        self._status = status
        if self.on_status_changed:
            self.on_status_changed(self.status)

    def connection_handler(self, connected, reason) -> None:
        if not connected:
            self.status = SessionCommunicator.Status.DISCONNECTED
            return

        self.status = SessionCommunicator.Status.CONNECTED

        # Subscribe to session topic
        def callback(success: bool):
            if success:
                self.status = SessionCommunicator.Status.SUBSCRIBED
        self.subscribe(f"swarm/session/{self.session_id}/#", callback)

    def control_message_handler(self, client, obj, msg):
        client_id = int(msg.topic.split('/')[-1])
        payload = json.loads(msg.payload)
        session_id = int(msg.topic.split('/')[-3])
        msg_type = payload.get('type', '')
        if msg_type == 'ready':
            self.on_participant_ready(client_id)
        elif msg_type == 'leave':
            self.on_participant_leave(session_id,client_id)
        else:
            print("Unknown message received in control topic")
    def control_admin_message_handler(self, client, obj, msg):

        payload = json.loads(msg.payload)
        msg_type = payload.get('type', '')
        if msg_type == 'setup':
            self.on_setup_question(str(payload.get('collection_id','')),str(payload.get('question_id','')));
        else:
            if msg_type == 'start':
                self.on_session_start(int(payload.get('duration', '')));
            else:
                if msg_type == 'stop':
                    self.on_session_stop(payload.get('mode', ''));
                else:
                    print("Unknown message received in control topic")
                    # TODO: Implement a 'keep-alive' mechanism: participants must send keep-alive messages
                    #       periodically so the server can determine if they have left without notifying

    def updates_message_handler(self, client, obj, msg):
        client_id = int(msg.topic.split('/')[-1])
        payload = json.loads(msg.payload)
        if self.on_participant_update:
            self.on_participant_update(client_id, payload.get('data', {}))

class Session():
    '''
        Contains all attributes, methods and events to handle a SWARM Session.
    '''
    last_id = 0
    regular_expresion= '%Y-%m-%d-%H-%M-%S'
    class Status(Enum):
        WAITING = 'waiting' # Waiting for clients to join
        ACTIVE = 'active'   # The Swarm Session is active (answering a question)
        # TODO: There should be at least an additional state where the server is
        #       waiting for clients to get ready. This would be useful for the GUI
        #       to check if the session can start or not

    def __init__(self):
        if ctx.AppContext.mqtt_broker is None:
            raise RuntimeError("MQTT broker not started")

        Session.last_id += 1
        self.id = Session.last_id
        self._status = Session.Status.WAITING
        self._question = None
        self._collection = None
        self.duration = 10
        self.participants: Dict[Participant] = {}
        self.log_file: Optional[TextIOBase] = None
        self.resume_file: Optional[TextIOBase] = None
        self.last_session_time = None
        self.target_date = None
        self.answers = {}
        # Obtén las claves (nombres de las colecciones) del diccionario de colecciones
        collection_keys = list(ctx.AppContext.collections.keys())
        # Verifica si hay al menos una colección en el diccionario
        if collection_keys:
            # Obtén el conjunto de objetos (preguntas) para la primera colección
            first_collection_questions = ctx.AppContext.collections[collection_keys[0]]
            # Si hay al menos una pregunta en la colección
            if first_collection_questions:
                self._collection = collection_keys[0]
                # Obtén el nombre de la primera pregunta (objeto)
                self._question = list(first_collection_questions)[0]

        self.communicator = SessionCommunicator(self.id, port=ctx.AppContext.mqtt_broker.port)
        self.communicator.on_participant_ready = self.participant_ready_handler
        self.communicator.on_participant_leave = self.participant_leave_handler
        self.communicator.on_participant_update = self.participant_update_handler
        self.communicator.on_session_start = self.session_start_handler
        self.communicator.on_setup_question = self.active_question
        self.communicator.on_session_stop = self.session_stop_handler

        self.communicator.start()

    def __eq__(self, other):
        return isinstance(other, Session) and self.id == other.id

    @property
    def status(self) -> Status:
        return self._status

    @status.setter
    def status(self, status: Status):
        self._status = status

    @property
    def ready_participants_count(self):
        return sum(
                participant.status == Participant.Status.READY
                for participant in self.participants.values()
            )

    @property
    def offline_participants_count(self):
        return sum(
                participant.status == Participant.Status.OFFLINE
                for participant in self.participants.values()
            )
    
    @property
    def as_dict(self):
        return {
            'id': self.id,
            'status': self._status.value,
            'question_id': self._question if self._question else None,
            'collection_id': self._collection if self._collection else None,
            'duration': self.duration,
        }

    def add_participant(self, username: str):
        in_list = False
        participant_return = None
        for participant in self.participants.values():
            if(participant.username.lower() == username.lower()):
                in_list = True
                participant.status = Participant.Status.JOINED
                participant_return = participant
        if(in_list != True):
            participant = Participant(username)
            self.participants[participant.id] = participant
            participant_return = participant
        return participant_return

    def remove_participant(self, participant_id: int):
        participant = self.participants.get(participant_id, None)
        if participant is None:
            print(f"ERROR: Participant [id={participant_id}] not found in Session [id={self.id}]")
            return
        participant.status = Participant.Status.OFFLINE

    def participant_ready_handler(self, participant_id: int):
        participant = self.participants.get(participant_id, None)
        if participant is None:
            print(f"ERROR: Participant [id={participant_id}] not found in Session [id={self.id}]")
            return
        def check_session_status ():
            if(self.status == Session.Status.ACTIVE and self.target_date is not None):
                self.communicator.publish(
                    f'swarm/session/{self.id}/control',
                    json.dumps({
                        'type': 'started',
                        'targetDate': self.target_date,
                        'positions': json.dumps(self.answers)
                    })
            )
        if(participant.status == Participant.Status.JOINED):
            participant.status = Participant.Status.READY
            check_session_status()

    def participant_leave_handler(self, session_id: int, participant_id: int):
        session = ctx.AppContext.sessions.get(session_id, None)
        session.remove_participant(participant_id)

    def active_question(self, collection: str,question: str):
        self._collection = collection
        self._question = question
        

    def session_start_handler(self, duration: int) -> bool:
        if(self.status == Session.Status.WAITING):
            self.duration =  duration
            self.last_session_time = datetime.now()
            log_folder = ctx.SESSION_LOG_FOLDER / self.last_session_time.strftime(self.regular_expresion)
            log_folder.mkdir(parents=True, exist_ok=True)
            with open(log_folder / 'session.json', 'w') as f:
                json.dump({
                    'time': self.last_session_time.isoformat(),
                    'id': self.id,
                    'collection': self._collection,
                    'question': self._question,
                    'duration': self.duration
                }, f, indent=4)

            self.log_file = open(log_folder / 'log.csv', 'w')
            self.resume_file = open(log_folder / 'resume.csv', 'w')
            self.status = Session.Status.ACTIVE

    def session_stop_handler(self,mode: str):
        def generate_zip():
                try:
                    folder_path = self.last_session_time.strftime(self.regular_expresion)
                    zip_filename = folder_path + ".zip"
                    folder_path = "./session_log/" + folder_path
                    zip_path = os.path.join("./session_log/zips", zip_filename)  # Ruta completa del archivo ZIP
                    with zipfile.ZipFile(zip_path, "w") as zipf:
                        for root, _, files in os.walk(folder_path):
                            for file in files:
                                file_path = os.path.join(root, file)
                                zipf.write(file_path, os.path.relpath(file_path, folder_path))
                except Exception as e:
                    print(f"Error al generar el archivo ZIP: {str(e)}")
        if(self.status == Session.Status.ACTIVE):
            log_folder = ctx.SESSION_LOG_FOLDER / self.last_session_time.strftime(self.regular_expresion)
            with open(log_folder / 'session.json', 'r+') as file:
                data = json.load(file)
                data['participants'] = ([participant.as_dict for participant in self.participants.values()])
                file.seek(0)
                json.dump(data, file, indent=4)

            if self.log_file:
                self.log_file.close()
                self.log_file = None
            if self.resume_file:
                for a in self.answers:
                    self.resume_file.write(f"{a},{self.answers[a]}\n")
                self.resume_file.close()
                self.resume_file = None
                self.answers = {}
            if mode == 'trajectories':
                convert_trajectory_files(log_folder)
            generate_zip()
            self.status = Session.Status.WAITING

    def participant_update_handler(self, participant_id: int, data: dict):
        position_data = data.get('position', None)
        timestamp = data.get('timeStamp', None)
        if position_data and timestamp:
            sum_position = 0
            for i, position in enumerate(position_data):
                if position < 0:
                    position_data[i] = 0
                sum_position += position_data[i]
            if sum_position > 1:
                for i in range(len(position_data)):
                    position_data[i] = position_data[i] / sum_position
            
            if not position_data:
                return
            if self.log_file:
                self.log_file.write(f"{participant_id},{timestamp},{','.join(str(e) for e in position_data)}\n")
                self.answers[participant_id] = timestamp + "," + ','.join(str(e) for e in position_data)
