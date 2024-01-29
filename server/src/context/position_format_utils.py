from __future__ import annotations
import datetime
import os
import csv
from dataclasses import dataclass
from pathlib import Path
import dateutil.parser
import numpy as np

RADIUS = 340
class PositionCodec:
    """Useful to transform the position from and to the format which the
    server requires"""
    
    def __init__(self, answer_points: np.ndarray):
        self.answer_points = answer_points
    
    def distance_squared(self, v1: np.ndarray, v2: np.ndarray, axis: int = -1):
        return ((v1 - v2) ** 2).sum(axis=axis)

    def distance(self, v1: np.ndarray, v2: np.ndarray, axis: int = -1) -> np.ndarray:
        return np.sqrt(distance_squared(v1, v2, axis))

    def encode(self, point: np.ndarray) -> np.ndarray:
        """Transforms the (2, ) numpy array into another numpy array in the format
        required by the hans platform"""

        distance_to_answers = distance(self.answer_points, point)
        closest_indices = np.argsort(distance_to_answers)[:2]
        new_basis = self.answer_points[closest_indices]
        point_new_basis = np.linalg.solve(new_basis.T, point)

        encoded_position = np.zeros(self.answer_points.shape[0])
        encoded_position[closest_indices] = point_new_basis

        return encoded_position

    def decode(self, encoded_position: np.ndarray) -> np.ndarray:
        """Decodes the format sent by the hans platform"""

        if len(encoded_position) != self.answer_points.shape[0]:
            print(f"Encoded position size: {len(encoded_position)}, Answer points size: {self.answer_points.shape[0]}")
            raise ValueError("Mismatch in the size of encoded_position and answer_points")

        return self.answer_points.T @ encoded_position

@dataclass
class PositionRecord:
    participant_id: int
    timestamp: datetime.datetime
    position: np.ndarray

    @classmethod
    def from_csv_row(cls, csv_row, pcodec):
        encoded_positions = list(map(float, csv_row[2:]))
        if len(encoded_positions) != pcodec.answer_points.shape[0]:
            print(encoded_positions)
            print(f"Encoded positions size: {len(encoded_positions)}, Answer points size: {pcodec.answer_points.shape[0]}")
        return cls(
            int(csv_row[0]),
            dateutil.parser.parse(csv_row[1]),
            pcodec.decode(np.array(encoded_positions)),
        )


@dataclass
class TrajectoryPoint:
    # Normalized vector so that, in case the radius is changed, the trajectory
    # of the data is still useful
    norm_position: np.ndarray

    # elapsed seconds since the first position
    timestamp: float

    def to_csv_row(self):
        x, y = self.norm_position
        return f"{self.timestamp},{x},{y}"

def _last_part(path):
        return os.path.basename(os.path.normpath(path))

def transform_to_trajectory_points(records, radius):
    if len(records) == 0:
        return []

    offset = records[0].timestamp
    trajectory_points = [
        TrajectoryPoint(
            norm_position=record.position / radius,
            timestamp=(record.timestamp - offset).total_seconds(),
        )
        for record in records
    ]

    return trajectory_points


def create_trajectory_file(path, target_path, pcodec, filename):
    filename += ".txt"

    with open(os.path.join(path, "log.csv")) as f:
        reader = csv.reader(f)
        records = [
            PositionRecord.from_csv_row(row, pcodec) for row in reader if row[0] != "0"
        ]
    trajectory_points = transform_to_trajectory_points(records, RADIUS)
    trajectory_rows = [trajectory_point.to_csv_row() for trajectory_point in trajectory_points]

    with open(os.path.join(target_path, filename), "w") as f:
        # TODO: change this hardcoded value
        f.write("0\n\n")
        f.writelines([f"{trajectory_row}\n" for trajectory_row in trajectory_rows])

def calculate_answer_points(num_answers: int, radius: float):
    # Keep in mind that the basis vector for the y axis points downwards. Therefore,
    # in this case, the first response will be drawn upwards (at pi / 2)

        angles = np.linspace(
            -np.pi / 2, -np.pi / 2 + 2 * np.pi, num_answers, endpoint=False
        )

        # We truncate because that is what is done in the original
        # (num_answers, 2)
        return np.trunc(
            radius * np.stack((np.cos(angles), np.sin(angles)), axis=1)
        )
def convert_trajectory_files(log_directory):

    pcodec = PositionCodec(calculate_answer_points(num_answers=6, radius=RADIUS))
    timestamp = os.path.basename(log_directory)
    target_path = Path('trajectories')
    create_trajectory_file(os.path.join(log_directory), target_path, pcodec, timestamp)