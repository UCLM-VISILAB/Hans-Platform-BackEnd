from pathlib import Path
from threading import Thread
import os
import zipfile
import shutil
import re
from flask import Flask, jsonify, send_from_directory, request
from werkzeug.serving import make_server
import boto3
from src.context import AppContext, Participant, Session


class ServerAPI(Thread):
    
    def __init__(self, host='0.0.0.0', port=8080):
        SESSION_NOT_FOUND = "Session not found"
        INVALID_REQUEST = "Invalid request"
        INVALID_CREDENTIALS="Check if your credentials are correct please"
        Thread.__init__(self)
        self.app = Flask(__name__, static_folder='../../../client/build')
        @self.app.route('/api/session/<int:session_id>', methods=['GET'])
        def api_session_handle_get(session_id: int):
            session = AppContext.sessions.get(session_id, None)
            if session is None:
                return SESSION_NOT_FOUND, 404

            return jsonify(session.as_dict)

        @self.app.route('/api/session', methods=['POST'])
        def api_get_all_sessions():
            print(request.json['user'])
            if 'user' not in request.json:
                return INVALID_REQUEST, 400
            username = request.json['user']
            password = request.json['pass']
            if(username!="admin" or password!="admin"):
                return INVALID_CREDENTIALS, 400
            
            return jsonify([session.as_dict for session in AppContext.sessions.values()])

        @self.app.route('/api/session/<int:session_id>', methods=['POST'])
        def api_edit_session(session_id: int):
            session = AppContext.sessions.get(session_id, None)
            if session is None:
                return SESSION_NOT_FOUND, 404

            session_data = request.json
            if any(
                key not in ['status', 'question_id', 'duration']
                for key in session_data.keys()
            ):
                return "Invalid parameter", 400

            if 'status' in session_data:
                try:
                    session.status = Session.Status(session_data['status'])
                except ValueError:
                    return "Requested status is not valid", 400

            if 'question_id' in session_data:
                question_id = session_data['question_id']
                if not isinstance(question_id, int):
                    return "Requested question_id must be an integer", 400

                session.active_question = question_id

            if 'duration' in session_data:
                if not isinstance(session_data['duration'], int):
                    return "Requested duration must be an integer", 400

                session.duration = session_data['duration']

            return jsonify(session.as_dict)

        @self.app.route('/api/session/<int:session_id>/allParticipants', methods=['POST'])
        def api_session_get_all_participants(session_id: int):
            if 'user' not in request.json:
                return INVALID_REQUEST, 400
            username = request.json['user']
            password = request.json['pass']
            if(username!="admin" or password!="admin"):
                return INVALID_CREDENTIALS, 400
            session = AppContext.sessions.get(session_id, None)
            if session is None:
                return SESSION_NOT_FOUND, 404
            return jsonify([participant.as_dict for participant in session.participants.values()])

        # función que escucha la petición del componente sessionLogin
        @self.app.route('/api/session/<int:session_id>/participants', methods=['POST'])
        def api_session_add_participant(session_id: int):
            if 'user' not in request.json:
                return INVALID_REQUEST, 400
            username = request.json['user']

            session = AppContext.sessions.get(session_id, None)
            if session is None:
                return SESSION_NOT_FOUND, 404
            if any(username.lower() == participant.username.lower() and participant.status!=Participant.Status.OFFLINE for participant in session.participants.values()):
                return "Participant already joined session", 400

            participant = session.add_participant(username)

            return jsonify(participant.as_dict)
        # Para poner en offline el status a un participante
        @self.app.route('/api/session/<int:session_id>/participants/<int:participant_id>', methods=['POST'])
        def api_session_remove_participant(session_id: int, participant_id: int):
            session = AppContext.sessions.get(session_id, None)
            if session is None:
                return SESSION_NOT_FOUND, 404

            if any(participant_id == participant.id for participant in session.participants.values()):
                if any(participant_id == participant.id and participant.status==Participant.Status.OFFLINE for participant in session.participants.values()):
                    return "Participant already leaved session", 400
                else:
                    session.remove_participant(participant_id)
            else:
                return "Participant not found", 404
            return "Bye"
        # Devuelve las colecciones enteras
        @self.app.route('/api/collection')
        def api_get_all_collections():
            collections = AppContext.collections
            if collections is None:
                return "Collections not found", 404
            else:
                # Convertir el conjunto en un diccionario antes de serializarlo en JSON
                collections_dict = {key: list(value) for key, value in collections.items()}
                return jsonify(collections_dict)
        # Devuelve una pregunta en concreto
        @self.app.route('/api/question/<string:collection>/<string:question_id>')
        def api_question_handle(collection: str, question_id: str):
            if collection in AppContext.collections:
                if question_id in AppContext.collections[collection]:
                    s3 = boto3.client('s3')
                    bucket_name = 'hans-platform-collections'

                    object_key = f'{collection}/{question_id}/info.json'
                    try:
                        response = s3.get_object(Bucket=bucket_name, Key=object_key)
                        info_json_content = response['Body'].read()
                        return info_json_content
                    except Exception as e:
                        print(f"No se pudo acceder al objeto {object_key}: {str(e)}")
                else:
                    return "Question not found", 404
            else:
                return "Collection not found", 404


            
        #Devuelve la imagen asociada a una pregunta
        @self.app.route('/api/question/<string:collection>/<string:question_id>/image')
        def api_question_image_handle(collection: str, question_id: str):
            if collection in AppContext.collections:
                if question_id in AppContext.collections[collection]:
                    s3 = boto3.client('s3')
                    bucket_name = 'hans-platform-collections'

                    object_key = f'{collection}/{question_id}/img.png'
                    try:
                        response = s3.get_object(Bucket=bucket_name, Key=object_key)
                        img_content = response['Body'].read()
                        return img_content
                    except Exception as e:
                        print(f"No se pudo acceder al objeto {object_key}: {str(e)}")
                else:
                    return "Question not found", 404
            else:
                return "Collection not found", 404

        # Serve client app
        @self.app.route('/', defaults={'path': ''})
        @self.app.route('/<path:path>')
        def client_handler(path):
            if path == '' or not (Path(self.app.static_folder) / path).is_file():
                return send_from_directory(self.app.static_folder, 'index.html')

            return send_from_directory(self.app.static_folder, path)

        # Peticiones para el admin
        @self.app.route('/api/admin/login', methods=['POST'])
        def api_admin_login():
            if 'user' not in request.json:
                return INVALID_REQUEST, 400
            username = request.json['user']
            password = request.json['pass']
            if(username!="admin" or password!="admin"):
                return INVALID_CREDENTIALS, 400
            
            return jsonify({"status":"ok"})
        # Crear una sesión
        @self.app.route('/api/createSession', methods=['POST'])
        def api_create_session():
            if 'user' not in request.json:
                return INVALID_REQUEST, 400
            username = request.json['user']
            password = request.json['pass']
            if(username!="admin" or password!="admin"):
                return INVALID_CREDENTIALS, 400
            session = Session()
            AppContext.sessions[session.id] = session
            return jsonify(session.as_dict)
        # Descarga un log en concreto
        @self.app.route('/api/downloadLog/<path:zip_filename>')
        def download_log(zip_filename):
            zip_filename+=".zip"
            zip_path = os.path.join("./session_log/zips", zip_filename)
            if os.path.isfile(zip_path):
                return send_from_directory(os.path.abspath(os.path.dirname(zip_path)), os.path.basename(zip_path), as_attachment=True)
            else:
                return "El archivo ZIP no existe", 404
        # Descarga todos los logs
        @self.app.route('/api/downloadAllLogs')
        def download_all_logs():
            zip_filepath = generate_all_logs_zip()

            if zip_filepath and os.path.isfile(zip_filepath):
                return send_from_directory(os.path.abspath(os.path.dirname(zip_filepath)), os.path.basename(zip_filepath), as_attachment=True)
            else:
                return "Error al generar el archivo ZIP", 500

        def generate_all_logs_zip():
            try:
                zip_filename = "AllLogs.zip"
                folder_path = "./session_log/zips"
                zip_path = os.path.join("./session_log", zip_filename)  # Ruta completa del archivo ZIP
                if os.path.isfile(zip_path):
                    os.remove(zip_path)
                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for root, _, files in os.walk(folder_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            zipf.write(file_path, os.path.relpath(file_path, folder_path))

                return zip_path
            except Exception as e:
                print(f"Error al generar el archivo ZIP: {str(e)}")
                return None

        # Devuelve una lista con los nombres de los logs
        @self.app.route('/api/listLogs')
        def list_logs():
            folder_path = './session_log'
            logs = [name for name in os.listdir(folder_path) if os.path.isdir(os.path.join(folder_path, name))]

            return jsonify(logs=logs)
        #Borra todos los logs
        @self.app.route('/api/deleteAllLogs')
        def delete_all_logs():
            session_log_path = "./session_log"
            zips_path = os.path.join(session_log_path, "zips")

            try:
                # Eliminar archivos dentro de ./session_log
                for filename in os.listdir(session_log_path):
                    file_path = os.path.join(session_log_path, filename)
                    if os.path.isfile(file_path) and filename != "AllLogs.zip":
                        os.unlink(file_path)

                # Eliminar carpetas dentro de ./session_log, excepto "zips"
                for folder_name in os.listdir(session_log_path):
                    folder_path = os.path.join(session_log_path, folder_name)
                    if os.path.isdir(folder_path) and folder_name != "zips":
                        shutil.rmtree(folder_path)

                # Eliminar archivos dentro de ./session_log/zips, excepto "AllLogs.zip"
                for filename in os.listdir(zips_path):
                    file_path = os.path.join(zips_path, filename)
                    if os.path.isfile(file_path) and filename != "AllLogs.zip":
                        os.unlink(file_path)

            except Exception:
                return "Error deleting logs", 500
            return jsonify({"status": "ok"})
        # Descarga todas las trayectorias
        @self.app.route('/api/downloadAllTrajectories')
        def download_all_trajectories():
            zip_filepath = generate_all_trajectories_zip()

            if zip_filepath and os.path.isfile(zip_filepath):
                return send_from_directory(os.path.abspath(os.path.dirname(zip_filepath)), os.path.basename(zip_filepath), as_attachment=True)
            else:
                return "Error al generar el archivo ZIP", 500
        def generate_all_trajectories_zip():
            try:
                zip_filename = "AllTrajectories.zip"
                folder_path = "./trajectories"
                zip_path = os.path.join(folder_path, zip_filename)  # Ruta completa del archivo ZIP

                # Eliminar el archivo ZIP si ya existe
                if os.path.isfile(zip_path):
                    os.remove(zip_path)

                with zipfile.ZipFile(zip_path, "w") as zipf:
                    for root, _, files in os.walk(folder_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # Verificar si el archivo que se va a agregar es el propio archivo ZIP
                            if file_path != zip_path:
                                zipf.write(file_path, os.path.relpath(file_path, folder_path))

                return zip_path
            except Exception as e:
                print(f"Error al generar el archivo ZIP: {str(e)}")
                return None
        # Borra todas las trayectorias
        @self.app.route('/api/deleteAllTrajectories')
        def delete_all_trajectories():
            folder_path = "./trajectories"
            session_path = "./session_log"
            zip_path = "./session_log/zips"

            try:
                # Verificar si la carpeta existe
                if os.path.exists(folder_path):
                    # Eliminar todo el contenido de la carpeta
                    for filename in os.listdir(folder_path):
                        file_path = os.path.join(folder_path, filename)
                        try:
                            if os.path.isfile(file_path) and filename.endswith(".txt"):
                                # Extraer la fecha del nombre del archivo usando una expresión regular
                                match = re.search(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})", filename)
                                if match:
                                    date_string = match.group(1)

                                    # Construir el nombre de la carpeta basado en la fecha
                                    subfolder_name = os.path.join(session_path, date_string)

                                    # Verificar si la carpeta existe y eliminarla
                                    if os.path.exists(subfolder_name):
                                        shutil.rmtree(subfolder_name)
                                    else:
                                        print(f"La carpeta {subfolder_name} no existe.")
                                    # Eliminar archivos .zip de la tercera carpeta
                                    if os.path.exists(zip_path):
                                        for zip_filename in os.listdir(zip_path):
                                            zip_file_path = os.path.join(zip_path, zip_filename)
                                            try:
                                                if os.path.isfile(zip_file_path) and zip_filename == date_string + ".zip":
                                                    os.unlink(zip_file_path)
                                            except Exception as e:
                                                print(f"No se pudo borrar {zip_file_path}: {e}")
                                    else:
                                        print("La tercera carpeta no existe.")
                                os.unlink(file_path)
                        except Exception as e:
                            print(f"No se pudo borrar {file_path}: {e}")
                else:
                    print("La carpeta no existe.")

            except Exception as e:
                return "Error deleting trajectories", 500
            return jsonify({"status": "ok"})


        self.server = make_server(host, port, self.app, threaded=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()
