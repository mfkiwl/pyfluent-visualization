from pathlib import Path
import pickle
from typing import Dict, List, Optional, Union

from ansys.fluent.core.services.field_data import SurfaceDataType
import numpy as np
import pytest

from ansys.fluent.visualization.matplotlib import Plots
from ansys.fluent.visualization.pyvista import Graphics


@pytest.fixture(autouse=True)
def patch_mock_api_helper(mocker) -> None:
    mocker.patch(
        "ansys.fluent.core.post_objects.post_helper.PostAPIHelper",
        MockAPIHelper,
    )


class MockFieldTransaction:
    def __init__(self, session_data, field_request):
        self.service = session_data
        self.fields_request = field_request

    def add_surfaces_request(
        self,
        surface_ids: List[int],
        overset_mesh: bool = False,
        provide_vertices=True,
        provide_faces=True,
        provide_faces_centroid=False,
        provide_faces_normal=False,
    ) -> None:
        self.fields_request["surf"].append(
            (
                surface_ids,
                overset_mesh,
                provide_vertices,
                provide_faces,
                provide_faces_centroid,
                provide_faces_normal,
            )
        )

    def add_scalar_fields_request(
        self,
        surface_ids: List[int],
        field_name: str,
        node_value: Optional[bool] = True,
        boundary_value: Optional[bool] = False,
    ) -> None:
        self.fields_request["scalar"].append(
            (surface_ids, field_name, node_value, boundary_value)
        )

    def add_vector_fields_request(
        self,
        surface_ids: List[int],
        field_name: str,
    ) -> None:
        self.fields_request["vector"].append((surface_ids, field_name))

    def get_fields(self) -> Dict[int, Dict]:
        fields = {}
        for request_type, requests in self.fields_request.items():
            for request in requests:
                if request_type == "surf":
                    tag_id = 0
                if request_type == "scalar":
                    location_tag = 4 if request[2] else 2
                    boundary_tag = 8 if request[3] else 0
                    tag_id = location_tag | boundary_tag
                if request_type == "vector":
                    tag_id = 0

                field_requests = fields.get(tag_id)
                if not field_requests:
                    field_requests = fields[tag_id] = {}
                surf_ids = request[0]
                for surf_id in surf_ids:
                    surface_requests = field_requests.get(surf_id)
                    if not surface_requests:
                        surface_requests = field_requests[surf_id] = {}
                    surface_requests.update(self.service["fields"][tag_id][surf_id])
        return fields


class MockFieldData:
    def __init__(self, solver_data, field_info):
        self._session_data = solver_data
        self._request_to_serve = {"surf": [], "scalar": [], "vector": []}
        self._field_info = field_info

    def new_transaction(self):
        return MockFieldTransaction(self._session_data, self._request_to_serve)

    def get_surface_data(
        self,
        surface_name: str,
        data_type: Union[SurfaceDataType, int],
        overset_mesh: Optional[bool] = False,
    ) -> Dict:
        surfaces_info = self._field_info().get_surfaces_info()
        surface_ids = surfaces_info[surface_name]["surface_id"]
        self._request_to_serve["surf"].append(
            (
                surface_ids,
                overset_mesh,
                data_type == SurfaceDataType.Vertices,
                data_type == SurfaceDataType.FacesConnectivity,
                data_type == SurfaceDataType.FacesCentroid,
                data_type == SurfaceDataType.FacesNormal,
            )
        )
        enum_to_field_name = {
            SurfaceDataType.FacesConnectivity: "faces",
            SurfaceDataType.Vertices: "vertices",
            SurfaceDataType.FacesCentroid: "centroid",
            SurfaceDataType.FacesNormal: "face-normal",
        }

        tag_id = 0
        if overset_mesh:
            tag_id = self._payloadTags[FieldDataProtoModule.PayloadTag.OVERSET_MESH]
        return {
            surface_id: self._session_data["fields"][tag_id][surface_id][
                enum_to_field_name[data_type]
            ]
            for surface_id in surface_ids
        }


class MockFieldInfo:
    def __init__(self, solver_data):
        self._session_data = solver_data

    def get_scalar_field_range(
        self, field: str, node_value: bool = False, surface_ids: List[int] = []
    ) -> List[float]:
        if not surface_ids:
            surface_ids = [
                v["surface_id"][0]
                for k, v in self._session_data["surfaces_info"].items()
            ]
        minimum, maximum = None, None
        for surface_id in surface_ids:
            range = self._session_data["range"][field][surface_id][
                "node_value" if node_value else "cell_value"
            ]
            minimum = min(range[0], minimum) if minimum else range[0]
            maximum = max(range[1], maximum) if maximum else range[1]
        return [minimum, maximum]

    def get_scalar_fields_info(self) -> dict:
        return self._session_data["scalar_fields_info"]

    def get_vector_fields_info(self) -> dict:
        return self._session_data["vector_fields_info"]

    def get_surfaces_info(self) -> dict:
        return self._session_data["surfaces_info"]


class MockAPIHelper:
    _session_data = None
    _session_dump = "tests//session.dump"

    def __init__(self, obj=None):
        if not MockAPIHelper._session_data:
            with open(
                str(Path(MockAPIHelper._session_dump).resolve()),
                "rb",
            ) as pickle_obj:
                MockAPIHelper._session_data = pickle.load(pickle_obj)
        self.field_info = lambda: MockFieldInfo(MockAPIHelper._session_data)
        self.field_data = lambda: MockFieldData(
            MockAPIHelper._session_data, self.field_info
        )
        self.id = lambda: 1


def test_field_api():
    pyvista_graphics = Graphics(session=None, post_api_helper=MockAPIHelper)
    contour1 = pyvista_graphics.Contours["contour-1"]

    field_info = contour1._api_helper.field_info()
    field_data = contour1._api_helper.field_data()

    surfaces_id = [
        v["surface_id"][0] for k, v in field_info.get_surfaces_info().items()
    ]

    # Get vertices
    vertices_data = field_data.get_surface_data("wall", SurfaceDataType.Vertices)

    transaction = field_data.new_transaction()

    # Get multiple fields
    transaction.add_surfaces_request(
        surfaces_id[:1],
        provide_vertices=True,
        provide_faces_centroid=True,
        provide_faces=False,
    )
    transaction.add_scalar_fields_request(surfaces_id[:1], "temperature", True)
    transaction.add_scalar_fields_request(surfaces_id[:1], "temperature", False)
    fields = transaction.get_fields()

    surface_tag = 0
    vertices = fields[surface_tag][surfaces_id[0]]["vertices"]
    centroid = fields[surface_tag][surfaces_id[0]]["centroid"]

    node_location_tag = 4
    node_data = fields[node_location_tag][surfaces_id[0]]["temperature"]
    element_location_tag = 2
    element_data = fields[element_location_tag][surfaces_id[0]]["temperature"]

    # Compare vertices obtained by different APIs
    np.testing.assert_array_equal(vertices, vertices_data[next(iter(vertices_data))])
    assert len(vertices) == len(node_data) * 3
    assert len(centroid) == len(element_data) * 3


def test_graphics_operations():
    pyvista_graphics1 = Graphics(session=None)
    pyvista_graphics2 = Graphics(session=None)
    contour1 = pyvista_graphics1.Contours["contour-1"]
    contour2 = pyvista_graphics2.Contours["contour-2"]

    # create
    assert pyvista_graphics1 is not pyvista_graphics2
    assert pyvista_graphics1.Contours is pyvista_graphics2.Contours
    assert list(pyvista_graphics1.Contours) == ["contour-1", "contour-2"]

    contour2.field = "temperature"
    contour2.surfaces_list = contour2.surfaces_list.allowed_values

    contour1.field = "pressure"
    contour1.surfaces_list = contour2.surfaces_list.allowed_values[0]

    # copy
    pyvista_graphics2.Contours["contour-3"] = contour1()
    contour3 = pyvista_graphics2.Contours["contour-3"]
    assert contour3() == contour1()

    # update
    contour3.update(contour2())
    assert contour3() == contour2()

    # del
    assert list(pyvista_graphics1.Contours) == [
        "contour-1",
        "contour-2",
        "contour-3",
    ]
    del pyvista_graphics1.Contours["contour-3"]
    assert list(pyvista_graphics1.Contours) == ["contour-1", "contour-2"]


def test_contour_object():

    pyvista_graphics = Graphics(session=None)
    contour1 = pyvista_graphics.Contours["contour-1"]
    field_info = contour1._api_helper.field_info()

    # Surfaces allowed values should be all surfaces.
    assert contour1.surfaces_list.allowed_values == list(
        field_info.get_surfaces_info().keys()
    )

    # Invalid surface should raise exception.
    with pytest.raises(ValueError) as value_error:
        contour1.surfaces_list = "surface_does_not_exist"

    # Invalid surface should raise exception.
    with pytest.raises(ValueError) as value_error:
        contour1.surfaces_list = ["surface_does_not_exist"]

    # Should accept all valid surface.
    contour1.surfaces_list = contour1.surfaces_list.allowed_values

    # Field allowed values should be all fields.
    assert contour1.field.allowed_values == list(field_info.get_scalar_fields_info())

    # Important. Because there is no type checking so following passes.
    contour1.field = [contour1.field.allowed_values[0]]

    # Should accept all valid fields.
    contour1.field = contour1.field.allowed_values[0]

    # Invalid field should raise exception.
    with pytest.raises(ValueError) as value_error:
        contour1.field = "field_does_not_exist"

    # Important. Because there is no type checking so following test passes.
    contour1.node_values = "value should be boolean"

    # changing filled to False or setting clip_to_range should set node_value
    # to True.
    contour1.node_values = False
    assert contour1.node_values() == False
    contour1.filled = False
    assert contour1.node_values() == True
    # node value can not be set to False because Filled is False
    contour1.node_values = False
    assert contour1.node_values() == True

    contour1.filled = True
    contour1.node_values = False
    assert contour1.node_values() == False
    contour1.range.option = "auto-range-off"
    contour1.range.auto_range_off.clip_to_range = True
    assert contour1.node_values() == True

    contour1.range.option = "auto-range-on"
    assert contour1.range.auto_range_off is None

    contour1.range.option = "auto-range-off"
    assert contour1.range.auto_range_on is None

    # Range should adjust to min/max of node field values.
    contour1.node_values = True
    contour1.field = "temperature"
    surfaces_id = [
        v["surface_id"][0]
        for k, v in field_info.get_surfaces_info().items()
        if k in contour1.surfaces_list()
    ]

    range = field_info.get_scalar_field_range(
        contour1.field(), contour1.node_values(), surfaces_id
    )
    assert range[0] == pytest.approx(contour1.range.auto_range_off.minimum())
    assert range[1] == pytest.approx(contour1.range.auto_range_off.maximum())

    # Range should adjust to min/max of cell field values.
    contour1.node_values = False
    range = field_info.get_scalar_field_range(
        contour1.field(), contour1.node_values(), surfaces_id
    )
    assert range[0] == pytest.approx(contour1.range.auto_range_off.minimum())
    assert range[1] == pytest.approx(contour1.range.auto_range_off.maximum())

    # Range should adjust to min/max of node field values
    contour1.field = "pressure"
    range = field_info.get_scalar_field_range(
        contour1.field(), contour1.node_values(), surfaces_id
    )
    assert range[0] == pytest.approx(contour1.range.auto_range_off.minimum())
    assert range[1] == pytest.approx(contour1.range.auto_range_off.maximum())


def test_vector_object():

    pyvista_graphics = Graphics(session=None)
    vector1 = pyvista_graphics.Vectors["contour-1"]
    field_info = vector1._api_helper.field_info()

    assert vector1.surfaces_list.allowed_values == list(
        field_info.get_surfaces_info().keys()
    )

    with pytest.raises(ValueError) as value_error:
        vector1.surfaces_list = "surface_does_not_exist"

    with pytest.raises(ValueError) as value_error:
        vector1.surfaces_list = ["surface_does_not_exist"]

    vector1.surfaces_list = vector1.surfaces_list.allowed_values

    vector1.range.option = "auto-range-on"
    assert vector1.range.auto_range_off is None

    vector1.range.option = "auto-range-off"
    assert vector1.range.auto_range_on is None

    surfaces_id = [
        v["surface_id"][0]
        for k, v in field_info.get_surfaces_info().items()
        if k in vector1.surfaces_list()
    ]

    range = field_info.get_scalar_field_range("velocity-magnitude", False)
    assert range == pytest.approx(
        [
            vector1.range.auto_range_off.minimum(),
            vector1.range.auto_range_off.maximum(),
        ]
    )


def test_surface_object():

    pyvista_graphics = Graphics(session=None)
    surf1 = pyvista_graphics.Surfaces["surf-1"]
    field_info = surf1._api_helper.field_info()

    surf1.definition.type = "iso-surface"
    assert surf1.definition.plane_surface is None
    surf1.definition.type = "plane-surface"
    assert surf1.definition.iso_surface is None

    surf1.definition.plane_surface.creation_method = "xy-plane"
    assert surf1.definition.plane_surface.yz_plane is None
    assert surf1.definition.plane_surface.zx_plane is None

    surf1.definition.type = "iso-surface"
    iso_surf = surf1.definition.iso_surface

    assert iso_surf.field.allowed_values == list(field_info.get_scalar_fields_info())

    # Important. Because there is no type checking so following test passes.
    iso_surf.field = [iso_surf.field.allowed_values[0]]

    # Incorrect field should throw exception
    with pytest.raises(ValueError) as value_error:
        iso_surf.field = "field_does_not_exist"

    # Iso surface value should automatically update upon change in field.
    iso_surf.field = "temperature"
    range = field_info.get_scalar_field_range(iso_surf.field(), True)
    assert (range[0] + range[1]) / 2.0 == pytest.approx(iso_surf.iso_value())

    # Setting out of range should throw exception
    with pytest.raises(ValueError) as value_error:
        iso_surf.iso_value = range[1] + 0.001

    with pytest.raises(ValueError) as value_error:
        iso_surf.iso_value = range[0] - 0.001

    # Iso surface value should automatically update upon change in field.
    iso_surf.field = "pressure"
    range = field_info.get_scalar_field_range(iso_surf.field(), True)
    assert (range[0] + range[1]) / 2.0 == pytest.approx(iso_surf.iso_value())

    # New surface should be in allowed values for graphics.
    cont1 = pyvista_graphics.Contours["surf-1"]
    assert "surf-1" in cont1.surfaces_list.allowed_values

    # New surface is not available in allowed values for plots.
    matplotlib_plots = Plots(session=None, post_api_helper=MockAPIHelper)
    p1 = matplotlib_plots.XYPlots["p-1"]
    assert "surf-1" not in p1.surfaces_list.allowed_values

    # With local surface provider it becomes available.
    local_surfaces_provider = Graphics(session=None).Surfaces
    matplotlib_plots = Plots(
        session=None,
        post_api_helper=MockAPIHelper,
        local_surfaces_provider=local_surfaces_provider,
    )
    assert "surf-1" in p1.surfaces_list.allowed_values


def test_create_plot_objects():
    matplotlib_plots1 = Plots(session=None, post_api_helper=MockAPIHelper)
    matplotlib_plots2 = Plots(session=None, post_api_helper=MockAPIHelper)
    matplotlib_plots1.XYPlots["p-1"]
    matplotlib_plots2.XYPlots["p-2"]

    assert matplotlib_plots1 is not matplotlib_plots2
    assert matplotlib_plots1.XYPlots is matplotlib_plots2.XYPlots
    assert list(matplotlib_plots1.XYPlots) == ["p-1", "p-2"]


def test_xyplot_object():

    matplotlib_plots = Plots(session=None, post_api_helper=MockAPIHelper)
    p1 = matplotlib_plots.XYPlots["p-1"]
    field_info = p1._api_helper.field_info()

    assert p1.surfaces_list.allowed_values == list(
        field_info.get_surfaces_info().keys()
    )

    with pytest.raises(ValueError) as value_error:
        p1.surfaces_list = "surface_does_not_exist"

    with pytest.raises(ValueError) as value_error:
        p1.surfaces_list = ["surface_does_not_exist"]

    p1.surfaces_list = p1.surfaces_list.allowed_values

    assert p1.y_axis_function.allowed_values == list(
        field_info.get_scalar_fields_info()
    )

    # Important. Because there is no type checking so following passes.
    p1.y_axis_function = [p1.y_axis_function.allowed_values[0]]

    p1.y_axis_function = p1.y_axis_function.allowed_values[0]

    with pytest.raises(ValueError) as value_error:
        p1.y_axis_function = "field_does_not_exist"
