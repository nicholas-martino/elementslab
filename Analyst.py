"""
MIT License

Copyright (c) 2020 Nicholas Martino

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import os
import glob
import timeit
import zipfile
from shutil import copyfile

from rtree import index
from shapely.ops import cascaded_union
import geopandas as gpd
import osmnx as ox
import pandana as pdna
import pandas as pd
import pylab as pl
import rasterio
import requests
import seaborn as sns
import statsmodels.api as sm
from PIL import Image
from sklearn.cluster import KMeans
from Statistics.basic_stats import shannon_div
from graph_tool.all import *
from matplotlib.colors import ListedColormap
from pylab import *
from rasterio import features
from selenium import webdriver
from sklearn.preprocessing import MinMaxScaler
from selenium.webdriver.firefox.options import Options
from shapely.affinity import translate, scale
from shapely.geometry import *
from shapely.ops import nearest_points, linemerge
from skimage import morphology as mp


def download_file(url, filename=None):
    if filename is None: local_filename = url.split('/')[-1]
    else: local_filename = filename
    # NOTE the stream=True parameter below
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk: # filter out keep-alive new chunks
                    f.write(chunk)
                    # f.flush()
    return local_filename


class GeoBoundary:
    def __init__(self, municipality='City, State', crs=26910,
                 directory='/Users/nicholasmartino/GoogleDrive/Geospatial/'):
        print(f"\nCreating GeoSpatial class {municipality}")
        self.municipality = str(municipality)
        self.directory = directory
        self.gpkg = f"{self.directory}Databases/{self.municipality}.gpkg"
        self.city_name = str(self.municipality).split(',')[0]
        self.crs = crs

        try:
            self.boundary = gpd.read_file(self.gpkg, layer='land_municipal_boundary')
            self.bbox = self.boundary.total_bounds
            print("> land_municipal_boundary layer found")
        except: print("> land_municipal_boundary layer not found")

        try:
            self.nodes = gpd.read_file(self.gpkg, layer='network_nodes')
            self.links = gpd.read_file(self.gpkg, layer='network_links')
            print("> network_nodes & _links layers found")
        except: print("> network_nodes &| _links layer(s) not found")

        print(f"Class {self.city_name} created @ {datetime.datetime.now()}, crs {self.crs}\n")

    # Download and pre process data
    def update_databases(self, bound=True, net=True, census=False, icbc=False):
        # Download administrative boundary from OpenStreetMaps
        if bound:
            print(f"Downloading {self.city_name}'s administrative boundary from OpenStreetMaps")
            self.boundary = ox.gdf_from_place(self.municipality)
            # self.boundary.to_crs(crs=4326, epsg=4326, inplace=True)
            # self.boundary.to_crs(crs=26910, epsg=26910, inplace=True)
            self.boundary.to_file(self.gpkg, layer='land_municipal_boundary', driver='GPKG')
            self.boundary = gpd.read_file(self.gpkg, layer='land_municipal_boundary')
            self.boundary.to_crs(epsg=self.crs, inplace=True)
            self.bbox = self.boundary.total_bounds
            s_index = self.boundary.sindex

        # Download street networks from OpenStreetMaps
        if net:
            print(f"Downloading {self.city_name}'s street network from OpenStreetMaps")

            def save_and_open(ox_g, name=''):
                ox.save_graph_shapefile(ox_g, 'osm', self.directory)
                edges = gpd.read_file(self.directory+'osm/edges/edges.shp')
                nodes = gpd.read_file(self.directory+'osm/nodes/nodes.shp')

                edges.crs = 4326
                edges.to_crs(epsg=self.crs, inplace=True)
                edges.to_file(self.gpkg, layer=f"{name}_links", driver='GPKG')
                nodes.crs = 4326
                nodes.to_crs(epsg=self.crs, inplace=True)
                nodes.to_file(self.gpkg, layer=f'{name}_nodes', driver='GPKG')

                return nodes, edges

            network = ox.graph_from_place(self.municipality)
            st_nodes, st_edges = save_and_open(network, 'network')
            cycleway = ox.graph_from_place(self.municipality, infrastructure='way["cycleway"]')
            c_nodes, c_edges = save_and_open(cycleway, 'network_cycle')

            # Simplify links
            s_tol = 15
            s_links = st_edges
            s_links.geometry = st_edges.simplify(s_tol)

            print("Filtering networks from OpenStreetMap")

            # Filter Open Street Map Networks into Walking, Cycling and Driving
            def filter_highway(l):
                mask = []
                for j in self.links.highway:
                    mh = []
                    for i in l:
                        if (i in j) | (i == j): mh.append(True)
                    if len(mh) > 0:
                        mask.append(True)
                    else:
                        mask.append(False)
                return self.links.loc[mask]

            walking = ['bridleway', 'corridor', 'footway', 'living_street', 'path', 'pedestrian', 'residential',
                       'primary', 'road', 'secondary', 'service', 'steps', 'tertiary', 'track', 'trunk', 'unclassified']
            cycling = ['cycleway']
            driving = ['corridor', 'living_street', 'motorway', 'primary', 'primary_link', 'residential', 'road',
                       'secondary', 'secondary_link', 'service', 'tertiary', 'tertiary_link', 'trunk', 'trunk_link',
                       'unclassified']

            walking_net = filter_highway(walking)
            cycling_net = pd.concat([filter_highway(cycling), c_edges]).reset_index().drop('index', axis=1)
            cycling_net = cycling_net.drop_duplicates(subset=['geometry'])
            cycling_net.loc[:, 'length'] = cycling_net.geometry.length
            driving_net = filter_highway(driving)

            walking_net.to_file(self.gpkg, layer='network_walk')
            cycling_net.to_file(self.gpkg, layer='network_cycle')
            driving_net.to_file(self.gpkg, layer='network_drive')

            sbb = False
            if sbb:
                # Buffer endpoints and get centroid
                end_pts = gpd.GeoDataFrame(geometry=[Point(l.xy[0][0], l.xy[1][0]) for l in s_links.geometry] +
                                                    [Point(l.xy[0][1], l.xy[1][1]) for l in s_links.geometry])
                uu = gpd.GeoDataFrame(geometry=[pol.centroid for pol in end_pts.buffer(s_tol/2).unary_union]).unary_union

                # Snap edges to vertices
                lns = []
                for ln in s_links.geometry:
                    p0 = Point(ln.coords[0])
                    p1 = Point(ln.coords[1])
                    np0 = nearest_points(p0, uu)[1]
                    np1 = nearest_points(p1, uu)[1]
                    lns.append(LineString([np0, np1]))

                s_links['geometry'] = lns

            s_links.to_file(self.gpkg, layer='network_links_simplified')
            print("Street network from OpenStreetMap updated")

    def merge_csv(self, path):
        os.chdir(path)
        file_out = "merged.csv"
        if os.path.exists(file_out):
            os.remove(file_out)
        file_pattern = ".csv"
        list_of_files = [file for file in glob.glob('*'+file_pattern)]
        print(list_of_files)
        # Consolidate all CSV files into one object
        result_obj = pd.concat([pd.read_csv(file) for file in list_of_files])
        # Convert the above object into a csv file and export
        result_obj.to_csv(file_out, index=False, encoding="utf-8")
        df = pd.read_csv("merged.csv")
        full_path = os.path.realpath(__file__)
        path, filename = os.path.split(full_path)
        os.chdir(path)
        print('CSVs successfully merged')
        return df

    def elevation(self, hgt_file, lon, lat):
        SAMPLES = 1201  # Change this to 3601 for SRTM1
        with open(hgt_file, 'rb') as hgt_data:
            # Each data is 16bit signed integer(i2) - big endian(>)
            elevations = np.fromfile(hgt_data, np.dtype('>i2'), SAMPLES * SAMPLES) \
                .reshape((SAMPLES, SAMPLES))

            lat_row = int(round((lat - int(lat)) * (SAMPLES - 1), 0))
            lon_row = int(round((lon - int(lon)) * (SAMPLES - 1), 0))

            return elevations[SAMPLES - 1 - lat_row, lon_row].astype(int)

    # Network analysis
    def gravity(self):
        # WIP
        gdf = gpd.read_file(self.gpkg, layer='land_dissemination_area')
        flows = {'origin': [], 'destination': [], 'flow': []}
        for oid in gdf.DAUID:
            for did in gdf.DAUID:
                flows['origin'].append(oid)
                flows['destination'].append(did)
                population = gdf.loc[gdf.DAUID == oid]['pop'].reset_index(drop=True)[0]
                destinations = gdf.loc[gdf.DAUID == did]['dest_ct_lda'].reset_index(drop=True)[0]
                if destinations == 0: destinations = 1
                print(str(oid)+' to '+str(did)+': '+population+' people to '+str(destinations))
                flows['flow'].append(population * destinations)
                print(population * destinations)
        return self

    def centrality(self, run=True, osm=False, dual=False, axial=False, layer='network_links'):
        if run:
            rf = 3

            links = gpd.read_file(self.gpkg, layer=layer)
            start_time = timeit.default_timer()

            # Calculate azimuth of links
            def calculate_azimuth(df):
                df['azimuth'] = [math.degrees(math.atan2((ln.xy[0][1] - ln.xy[0][0]), (ln.xy[1][1] - ln.xy[1][0]))) for
                                    ln in df.geometry]
                pos_000_090 = df['azimuth'].loc[(df['azimuth'] > 0) & (df['azimuth'] < 90)]
                pos_090_180 = df['azimuth'].loc[(df['azimuth'] > 90) & (df['azimuth'] < 180)]
                neg_000_090 = df['azimuth'].loc[(df['azimuth'] < 0) & (df['azimuth'] > -90)]
                neg_090_180 = df['azimuth'].loc[(df['azimuth'] < -90)]
                tdf = pd.concat([pos_000_090, (90 - (pos_090_180 - 90)), neg_000_090 * -1, neg_090_180 + 180])
                df['azimuth_n'] = tdf
                return df

            if osm:
                # Create topological graph and add vertices
                osm_g = Graph(directed=False)
                nodes = gpd.read_file(self.gpkg, layer='network_nodes')
                links = calculate_azimuth(links)

                for i in list(nodes.index):
                    v = osm_g.add_vertex()
                    v.index = int(i)

                print(f"Processing {len(list(osm_g.vertices()))} vertices added to graph, {len(nodes)} nodes downloaded from OSM")

                # Graph from OSM topological data
                weights = []
                links_ids = []
                g_edges = []
                for i in list(links.index):
                    azim = links.at[i, 'azimuth_n']
                    geom = links.at[i, 'geometry']
                    o_osmid = links.at[i, 'from']
                    d_osmid = links.at[i, 'to']
                    o_id = nodes.loc[(nodes['osmid'] == o_osmid)].index[0]
                    d_id = nodes.loc[(nodes['osmid'] == d_osmid)].index[0]

                    # Find connected links
                    connected_links = links.loc[
                        (links['from'] == o_osmid) | (links['to'] == d_osmid) |
                        (links['from'] == d_osmid) | (links['to'] == o_osmid)
                    ]
                    c_links = connected_links.drop(i)
                    c_links = c_links.drop_duplicates()

                    # List edges and azimuths for dual graph
                    if o_osmid != d_osmid:
                        land_length = links.at[i, 'geometry'].length
                        topo_length = LineString([
                            nodes.loc[nodes.osmid == o_osmid].geometry.values[0],
                            nodes.loc[nodes.osmid == d_osmid].geometry.values[0]
                        ]).length

                        # Calculate indicators
                        straightness = land_length / topo_length
                        connectivity = len(c_links)

                        # Calculate azimuth similarity
                        tol = 45
                        diff = [max([cl, azim]) - min([cl, azim]) for cl in connected_links['azimuth_n']]
                        tolerable = [d for d in diff if d < tol]
                        ave_ang_diff = sum(diff)/len(diff)
                        # similarity = len(tolerable) / len(c_links)

                        if straightness > 1.57: ave_ang_diff = 90

                        w = (straightness * ave_ang_diff * land_length)
                        weights.append(w)
                        g_edges.append([int(o_id), int(d_id)])

                        links_ids.append(i)
                        g = osm_g

            if dual:
                s_tol = 15
                # Simplify links geometry
                links.loc[:, 'geometry'] = links.simplify(s_tol)

                # Explode links poly lines and calculate azimuths
                n_links = []
                for ln in links.geometry:
                    if len(ln.coords) > 2:
                        for i, coord in enumerate(ln.coords):
                            if i == len(ln.coords)-1: pass
                            else: n_links.append(LineString([Point(coord), Point(ln.coords[i + 1])]))
                    else: n_links.append(ln)

                links = gpd.GeoDataFrame(n_links, columns=['geometry'])
                links = calculate_azimuth(links)

                # Extract nodes from links
                links.dropna(subset=['geometry'], inplace=True)
                links.reset_index(inplace=True, drop=True)
                print(f"Processing centrality measures for {len(links)} segments using simplified dual graph")
                l_nodes = gpd.GeoDataFrame(geometry=[Point(l.xy[0][0], l.xy[1][0]) for l in links.geometry]+
                                                    [Point(l.xy[0][1], l.xy[1][1]) for l in links.geometry])

                # Pre process nodes
                rf = 3
                l_nodes['cid'] = [f'%.{rf}f_' % n.xy[0][0] + f'%.{rf}f' % n.xy[1][0] for n in l_nodes.geometry]
                l_nodes.drop_duplicates('cid', inplace=True)
                l_nodes.reset_index(inplace=True, drop=True)

                # Create location based id
                links['o_cid'] = [f'%.{rf}f_' % l.xy[0][0] + f'%.{rf}f' % l.xy[1][0] for l in links.geometry]
                links['d_cid'] = [f'%.{rf}f_' % l.xy[0][1] + f'%.{rf}f' % l.xy[1][1] for l in links.geometry]

                # Create topological dual graph
                dg = Graph(directed=False)

                # Iterate over network links indexes to create nodes of dual graph
                for i in list(links.index):
                    v = dg.add_vertex()
                    v.index = i

                # Iterate over network links geometries to create edges of dual graph
                azs = []
                g_edges = []
                weights = []
                links_ids = []
                for l, i in zip(links.geometry, list(links.index)):
                    links_ids.append(i)

                    # Get other links connected to this link
                    o = links.loc[links['o_cid'] == f'%.{rf}f_' % l.xy[0][0] + f'%.{rf}f' % l.xy[1][0]]
                    d = links.loc[links['d_cid'] == f'%.{rf}f_' % l.xy[0][1] + f'%.{rf}f' % l.xy[1][1]]

                    connected_links = pd.concat([o, d])
                    connected_links.drop_duplicates(inplace=True)

                    # Calculate azimuth similarity
                    tol = 45
                    azim = links.at[i, 'azimuth_n']
                    connected_links['ang_diff'] = [max([cl, azim]) - min([cl, azim]) for cl in connected_links['azimuth_n']]
                    tolerable = [d for d in connected_links['ang_diff'] if d < tol]
                    ave_ang_diff = sum(connected_links['ang_diff']) / len(connected_links['ang_diff'])
                    connected_links['ang_conn'] = connected_links['geometry'].length * connected_links['ang_diff']
                    ave_ang_conn = sum(connected_links['ang_conn']) / len(connected_links['ang_conn'])
                    weights.append(ave_ang_diff * links.at[i, 'geometry'].length)

                    # List edges and azimuths for dual graph
                    for j in list(connected_links.index):
                        azimuths = [links.at[i, 'azimuth_n'], links.at[j, 'azimuth_n']]
                        g_edges.append([i, j, connected_links.at[j, 'ang_diff']])

                g = dg
                nodes = links

            if axial:
                s_tol = 15
                connectivity = []
                # Simplify links geometry
                links.loc[:, 'geometry'] = links.simplify(s_tol)

                # Explode links poly lines and calculate azimuths
                n_links = []
                for ln in links.geometry:
                    if len(ln.coords) > 2:
                        for i, coord in enumerate(ln.coords):
                            if i == len(ln.coords)-1: pass
                            else: n_links.append(LineString([Point(coord), Point(ln.coords[i + 1])]))
                    else: n_links.append(ln)

                links = gpd.GeoDataFrame(n_links, columns=['geometry'])
                links = calculate_azimuth(links)
                links = links.reset_index()

                kmeans = KMeans(n_clusters=6)
                kmeans.fit(links.azimuth_n.values.reshape(-1,1))
                links['axial_labels'] = kmeans.labels_

                clusters = [links.loc[links.axial_labels == i] for i in links['axial_labels'].unique()]
                mpols = [df.buffer(2).unary_union for df in clusters]
                geoms = []
                for mpol in mpols:
                    for pol in mpol:
                        geoms.append(pol)
                axial_gdf = gpd.GeoDataFrame(geometry=geoms)
                axial_gdf = axial_gdf.reset_index()

                g = Graph(directed=False)
                for i, pol in enumerate(axial_gdf.geometry):
                    v = g.add_vertex()
                    v.index = i

                axial_gdf['id'] = axial_gdf.index
                axial_gdf['length'] = axial_gdf.buffer(1).area
                links.to_file(self.gpkg, layer='network_axial')
                idx = index.Index()

                # Populate R-tree index with bounds of grid cells
                for pos, cell in enumerate(axial_gdf.geometry):
                    # assuming cell is a shapely object
                    idx.insert(pos, cell.bounds)

                g_edges = []
                # Loop through each Shapely polygon (axial line)
                for i, pol in enumerate(axial_gdf.geometry):

                    # Merge cells that have overlapping bounding boxes
                    potential_conn = [axial_gdf.id[pos] for pos in idx.intersection(pol.bounds)]

                    # Now do actual intersection
                    conn = []
                    for j in potential_conn:
                        if axial_gdf.loc[j, 'geometry'].intersects(pol):
                            if [j, i, 1] in g_edges: pass
                            else:
                                g_edges.append([i, j, 1])
                                conn.append(j)
                    connectivity.append(len(conn))
                    print(f"> Finished adding edges of axial line {i} to dual graph")

                links = axial_gdf
                links['connectivity'] = connectivity

            if osm:
                # Log normalization
                log = False
                if log:
                    weights_n = np.log(weights)
                else:
                    weights_n = weights
                weights_n = [(x - min(weights_n)) / (max(weights_n) - min(weights_n)) for x in weights_n]

                for edge, w in zip(g_edges, weights_n):
                    edge.append(w)

            straightness = g.new_edge_property("double")
            edge_properties = [straightness]
            print("> All links and weights listed, creating graph")
            g.add_edge_list(g_edges, eprops=edge_properties)

            btw = betweenness(g, weight=straightness)
            clo = closeness(g, weight=straightness)

            def clean(col):
                btw_min = col.sort_values().unique()[0]
                btw_max = col.sort_values().unique()[len(col.unique())-3]
                col.replace(np.inf, btw_max, inplace=True)
                col.replace(-np.inf, btw_min, inplace=True)
                col.fillna(btw_min, inplace=True)
                return col

            if osm:
                # Calculate centrality measures and assign to nodes
                nodes['closeness'] = clo.get_array()
                nodes['betweenness'] = btw[0].get_array()
                nodes['closeness'] = clean(nodes['closeness'])

                # Assign betweenness to links
                l_betweenness = pd.DataFrame(links_ids, columns=['ids'])
                l_betweenness['betweenness'] = btw[1].get_array()
                l_betweenness.index = l_betweenness.ids
                for i in links_ids:
                    links.at[i, 'betweenness'] = l_betweenness.at[i, 'betweenness']

            if dual | axial:
                links['closeness'] = clo.get_array()
                links['betweenness'] = btw[0].get_array()

            """
            # Replace infinity and NaN values
            rep = lambda col: col.replace([-np.inf], np.nan, inplace=True)
            for c in [nodes['closeness'], nodes['betweenness'], links['betweenness']]:
                rep(c)
            """

            # Clean and normalize
            links['closeness'] = clean(links['closeness'])
            links['betweenness'] = clean(links['betweenness'])
            links['n_betweenness'] = np.log(links['betweenness'])
            links['n_betweenness'] = clean(links['n_betweenness'])

            # Export to GeoPackage
            if osm:
                links.to_file(self.gpkg, layer=layer)
                nodes.to_file(self.gpkg, layer='network_nodes')

            if dual:
                links.to_file(self.gpkg, layer='network_simplified')

            if axial:
                links.to_file(self.gpkg, layer='network_axial')

            elapsed = round((timeit.default_timer() - start_time) / 60, 1)
            print(f"Centrality measures processed in {elapsed} minutes")
            return links

    def network_analysis(self, sample_gdf, aggregated_layers, service_areas, run=True):
        """
        Given a layer of spatial features, it aggregates data from its surroundings using network service areas

        :param sample_layer: (str) Sample features to be analyzed, ex: 'lda' or 'parcel'.
        :param aggregated_layers: (dict) Layers and columns to aggregate data, ex: {'lda':["walk"], 'parcel':["area"]}
        :param service_areas: (list) Buffer to aggregate from each sample_layer feature[400, 800, 1600]
        :return:
        """

        if run:
            print(f'> Network analysis for {len(sample_gdf.geometry)} geometries at {service_areas} buffer radius')
            start_time = timeit.default_timer()

            # Load data
            nodes = self.nodes
            edges = self.links
            print(nodes.head(3))
            print(edges.head(3))
            nodes.index = nodes['osmid']

            # Reproject GeoDataFrames
            sample_gdf.to_crs(epsg=self.crs, inplace=True)
            nodes.to_crs(epsg=self.crs, inplace=True)
            edges.to_crs(epsg=self.crs, inplace=True)

            # Create network
            net = pdna.Network(nodes.geometry.x,
                               nodes.geometry.y,
                               edges["from"],
                               edges["to"],
                               edges[["length"]],
                               twoway=True)
            print(net)
            net.precompute(max(service_areas))

            x, y = sample_gdf.centroid.x, sample_gdf.centroid.y
            sample_gdf["node_ids"] = net.get_node_ids(x.values, y.values)

            buffers = {}
            for key, values in aggregated_layers.items():
                values = [f"{key}_ct"]+values
                gdf = gpd.read_file(self.gpkg, layer=key)
                gdf.to_crs(epsg=self.crs, inplace=True)
                x, y = gdf.centroid.x, gdf.centroid.y
                gdf["node_ids"] = net.get_node_ids(x.values, y.values)
                gdf[f"{key}_ct"] = 1

                # Try to convert to numeric
                uniques = {}
                for value in values:
                    try: pd.to_numeric(gdf[value])
                    except:
                        uniques[value] = []
                        for item in gdf[value].unique():
                            gdf.loc[gdf[value] == item, item] = 1
                            gdf.loc[gdf[value] != item, item] = 0
                            values.append(item)
                            uniques[value].append(item)
                        values.remove(value)

                for value in values:
                    print(f'> Processing {value} column from {key} GeoDataFrame')
                    net.set(node_ids=gdf["node_ids"], variable=gdf[value])

                    for radius in service_areas:

                        cnt = net.aggregate(distance=radius, type="count", decay="flat")
                        sm = net.aggregate(distance=radius, type="sum", decay="flat")
                        ave = net.aggregate(distance=radius, type="ave", decay='flat')

                        sample_gdf[f"{key}_r{radius}_cnt"] = list(cnt.loc[sample_gdf["node_ids"]])
                        sample_gdf[f"{value}_r{radius}_sum"] = list(sm.loc[sample_gdf["node_ids"]])
                        sample_gdf[f"{value}_r{radius}_ave"] = list(ave.loc[sample_gdf["node_ids"]])

                # Calculate diversity index for categorical variables
                for key, values in uniques.items():
                    for radius in service_areas:
                        ns = []
                        ns_op = []
                        for category in values:
                            n = sample_gdf.loc[:, f"{category}_r{radius}_sum"]
                            ns.append(n)
                            ns_op.append(n * (n - 1))
                        dividend = pd.concat(ns_op, axis=1).sum(axis=1)
                        N = pd.concat(ns, axis=1).sum(axis=1)
                        divisor = N * (N - 1)
                        diversity = dividend/divisor
                        sample_gdf[f"{key}_r{radius}_div"] = 1 - diversity

            # Clean count columns
            for col in sample_gdf.columns:
                if ('_ct_' in col) & ('_cnt' in col): sample_gdf = sample_gdf.drop([col], axis=1)
                try:
                    if ('_ct_' in col) & ('_sum' in col): sample_gdf = sample_gdf.drop([col], axis=1)
                except: pass

            elapsed = round((timeit.default_timer() - start_time) / 60, 1)
            sample_gdf.to_file(self.gpkg, layer=f'network_analysis', driver='GPKG')
            print(f'Network analysis processed in {elapsed} minutes @ {datetime.datetime.now()}, regressing data')

            # Get name of features analyzed within the service areas
            new_features = []
            for col in sample_gdf.columns:
                for radius in service_areas:
                    id = f'_r{radius}_'
                    if id in col:
                        new_features.append(col)

            return new_features

    def network_from_polygons(self, filepath='.gpkg', layer='land_assessment_parcels', remove_islands=False,
                              scale_factor=0.82, tolerance=4, buffer_radius=10, min_lsize=20, max_linters=0.5):
        """
        Input a set of polygons and generate linear networks within the center of empty spaces among features.

        Params:
        filepath (str) = Directory for the GeoDatabase (i.e.: .gdb, .gpkg) with the polygons
        layer (str) = Polygon layer name within the GeoDatabase
        tolerance (float) = Tolerance for edges simplification
        buffer_radius (float) = Radius of intersection buffer for node simplification
        """
        s = 0
        figname = 'hq'
        sf = scale_factor

        print(f"> Processing centerlines for {layer} from {self.gpkg}")
        start_time = timeit.default_timer()

        # Read GeoDatabase
        gdf = gpd.read_file(filepath, layer=layer, driver='GPKG')
        gdf.dropna(subset=['geometry'], inplace=True)
        gdf.to_crs(epsg=self.crs, inplace=True)
        gdf_uu = gdf.geometry.unary_union

        # Extract open spaces
        try: chull = gpd.GeoDataFrame(geometry=[self.boundary.buffer(10)], crs=gdf.crs)
        except: chull = gpd.GeoDataFrame(geometry=[gdf_uu.convex_hull.buffer(10)], crs=gdf.crs)
        empty = gpd.overlay(chull, gdf, how='difference')

        # Export open spaces to image file
        empty.plot()
        plt.axis('off')
        plt.savefig(f'{figname}.png', dpi=600)

        # Create network_from_polygons from black and white raster
        tun = 1 - pl.imread(f'{figname}.png')[..., 0]
        skl = mp.medial_axis(tun)

        # Display and save centerlines
        image = Image.fromarray(skl)
        image.save(f'{figname}_skltn.png')

        # Load centerlines image
        with rasterio.open(f'{figname}_skltn.png') as src:
            blue = src.read()
        mask = blue != 0

        # Transform raster into shapely geometry (vectorize)
        shapes = features.shapes(blue, mask=mask)
        cl_pxl = gpd.GeoDataFrame(geometry=[Polygon(shape[0]['coordinates'][0]) for shape in shapes], crs=gdf.crs)

        # Buffer polygons to form centerline polygon
        cl_pxl_sc = gpd.GeoDataFrame(geometry=[scale(cl_pxl.buffer(-0.1).unary_union, sf, -sf, sf)], crs=gdf.crs)

        # Geo reference edges based on centroids
        raw_centr = gdf.unary_union.convex_hull.buffer(10).centroid
        xoff = raw_centr.x - cl_pxl_sc.unary_union.convex_hull.centroid.x  # dela.centroid.x
        yoff = raw_centr.y - cl_pxl_sc.unary_union.convex_hull.centroid.y  # dela.centroid.y

        # Translate, scale down and export
        cl_pxl_tr = gpd.GeoDataFrame(geometry=[translate(cl_pxl_sc.unary_union, xoff=xoff, yoff=yoff, zoff=0.0)], crs=gdf.crs)

        # Intersect pixels and vectorized center line to identify potential nodes of the network
        cl_b_mpol = gpd.GeoDataFrame(geometry=[cl_pxl_tr.buffer(2).unary_union], crs=gdf.crs)

        # Negative buffer to find potential nodes
        buffer_r = -2.8
        print(f"> {len(cl_b_mpol.buffer(buffer_r).geometry[0])} potential nodes identified")

        # Buffer and subtract
        node_buffer = gpd.GeoDataFrame(
            geometry=[pol.centroid.buffer(buffer_radius) for pol in cl_b_mpol.buffer(buffer_r).geometry[0]],
            crs=gdf.crs)
        difference = gpd.overlay(node_buffer, cl_b_mpol, how="difference")
        difference['mpol_len'] = [len(mpol) if type(mpol)==type(MultiPolygon()) else 1 for mpol in difference.geometry]
        p_nodes = difference.loc[difference['mpol_len'] > 2]

        # Extract nodes that intersect more than two links
        node = node_buffer.iloc[difference.index]
        node['n_links'] = difference['mpol_len']
        node = node.loc[node['n_links'] > 2].centroid
        node = gpd.GeoDataFrame(geometry=[pol.centroid for pol in node.buffer(6).unary_union], crs=gdf.crs)

        # Buffer extracted nodes
        cl_b2 = gpd.GeoDataFrame(geometry=cl_pxl_tr.buffer(2).boundary)
        cl_b1 = gpd.GeoDataFrame(geometry=cl_pxl_tr.buffer(1))
        cl_b1.to_file(self.gpkg, layer=f'network_centerline')

        # Subtract buffered nodes from center line polygon
        node_b6 = gpd.GeoDataFrame(geometry=node.buffer(6), crs=gdf.crs)
        node_b9 = gpd.GeoDataFrame(geometry=node.buffer(9), crs=gdf.crs)
        node_b12 = gpd.GeoDataFrame(geometry=node.buffer(12), crs=gdf.crs)

        # Subtract buffered nodes from centerline polygon and simplify
        links = gpd.overlay(cl_b2, node_b6, how='difference').simplify(tolerance)

        # Find link vertices (changes in direction)
        snapping = gpd.GeoDataFrame()
        for ln in links.geometry[0]:
            # Extract vertices from lines and collapse close vertices
            vertices = gpd.GeoDataFrame(geometry=[Point(coord) for coord in ln.coords], crs=gdf.crs)
            try: vertices = gpd.GeoDataFrame(geometry=[pol.centroid for pol in vertices.buffer(buffer_radius).unary_union], crs=gdf.crs)
            except: vertices = gpd.GeoDataFrame(geometry=vertices.buffer(buffer_radius).centroid, crs=gdf.crs)
            # Eliminate vertices if its buffer intersects with the network_nodes
            vertices = vertices[vertices.disjoint(node_b6.unary_union)]
            snapping = pd.concat([snapping, vertices])
        # Simplify and export
        snapping.reset_index(inplace=True)
        vertices = gpd.GeoDataFrame(geometry=[pol.centroid for pol in snapping.buffer(buffer_radius).unary_union], crs=gdf.crs)
        vertices = vertices[vertices.disjoint(node_b12.unary_union)]
        vertices = pd.concat([vertices, node])

        # Extract and explode line segments
        links_exploded = []
        for ln in links.geometry[0]:
            if type(ln) == type(MultiLineString()):
                coords = [l.coords for l in ln]
            else: coords = ln.coords
            for i, coord in enumerate(coords):
                if i < len(coords)-1: links_exploded.append(LineString([Point(coords[i]), Point(coords[i+1])]))
        links_e = gpd.GeoDataFrame(geometry=links_exploded, crs=gdf.crs)

        # Snap edges to vertices
        lns = []
        for ln in links_exploded:
            p0 = Point(ln.coords[0])
            p1 = Point(ln.coords[1])
            np0 = nearest_points(p0, vertices.unary_union)[1]
            np1 = nearest_points(p1, vertices.unary_union)[1]
            lns.append(LineString([np0, np1]))

        # Create GeoPackage with links
        edges = gpd.GeoDataFrame(geometry=lns, crs=gdf.crs)

        # Drop links smaller than a certain length only connected to one node
        for i, link in enumerate(edges.geometry):
            if float(link.length) < min_lsize:
                try: len(link.intersection(node_b6.unary_union))
                except:
                    edges.drop(index=i, inplace=True)
                    print(f"> Link at index {i} have only one connected node and its length is smaller than threshold")

        # Create centroid id field, drop duplicate geometry and export to GeoPackage
        edges['cid'] = [str(ln.centroid) for ln in edges.geometry]
        edges.drop_duplicates(['cid'], inplace=True)
        edges.reset_index(inplace=True, drop=True)
        edges['index'] = list(edges.index)
        edges['azimuth'] = [math.degrees(math.atan2((ln.xy[0][1] - ln.xy[0][0]), (ln.xy[1][1] - ln.xy[1][0]))) for ln in
                            edges.geometry]
        edges['length'] = [ln.length for ln in edges.geometry]

        vertices.reset_index(inplace=True)
        # Iterate over vertices
        for i, v in enumerate(vertices.geometry):
            # Remove isolated vertices
            if v.buffer(2).disjoint(edges.unary_union):
                vertices.drop(index=i, inplace=True)

            # Remove lines with close azimuth
            edges_in = edges[edges.intersects(v)]
            edges_in.reset_index(inplace=True)

            if len(edges_in) == 1: pass
            else:
                # Compare origin, destination and centroids of each line intersecting vertices
                for i, ln0 in enumerate(edges_in.geometry):
                    # If iteration is in the last item set ln1 to be the first line
                    if i == len(edges_in)-1:
                        li1 = edges_in.at[0, 'index']
                        ln1 = edges_in.at[0, 'geometry']
                    else:
                        li1 = edges_in.at[i+1, 'index']
                        ln1 = edges_in.at[i+1, 'geometry']

                    inters_bf = 4
                    inters0 = ln0.buffer(inters_bf).intersection(ln1.buffer(inters_bf)).area/ln0.buffer(inters_bf).area
                    inters1 = ln1.buffer(inters_bf).intersection(ln0.buffer(inters_bf)).area/ln1.buffer(inters_bf).area
                    inters = max(inters0, inters1)

                    li0 = edges_in.at[i, 'index']
                    if inters > max_linters:
                        if ln0.length < ln1.length:
                            try: edges.drop(li0, axis=0, inplace=True)
                            except: pass
                            print(f"> Link {li0} dropped due to similarity with another edge above threshold {max_linters}")
                        else:
                            try: edges.drop(li1, axis=0, inplace=True)
                            except: pass
                            print(f"> Link {li1} dropped due to similarity with another edge above threshold {max_linters}")

        # Remove nodes that are not intersections
        edges_b2 = gpd.GeoDataFrame(geometry=[edges.buffer(2).unary_union])
        difference = gpd.overlay(node_b6, edges_b2, how="difference")
        difference['mpol_len'] = [len(mpol) if type(mpol)==type(MultiPolygon()) else 1 for mpol in difference.geometry]
        node = node.loc[difference['mpol_len'] > 2]
        node.to_file(self.gpkg, layer='network_nodes')

        # Remove islands
        if remove_islands: edges = edges[edges.intersects(node.unary_union)]

        # Export links and vertices
        edges.to_file(self.gpkg, driver='GPKG', layer=f'network_links')
        vertices.to_file(self.gpkg, layer='network_vertices')

        elapsed = round((timeit.default_timer() - start_time) / 60, 1)
        return print(f"Centerlines processed in {elapsed} minutes @ {datetime.datetime.now()}")

    def density_ratios(self, network=True, land=True):

        if network:
            links = gpd.read_file(self.gpkg, layer='network_links')

        if land:
            pass
            # Calculate FAR
            # Calculate population density
        return None

    # Spatial analysis
    def set_parameters(self, service_areas, unit='lda', samples=None, max_area=7000000, elab_name='Sunset', bckp=True,
                       layer='Optional GeoPackage layer to analyze', buffer_type='circular'):
        # Load GeoDataFrame and assign layer name for LDA
        if unit == 'lda':
            gdf = self.DAs.loc[self.DAs.geometry.area < max_area]
            layer = 'land_dissemination_area'

        # Pre process database for elementslab 1600x1600m 'Sandbox'
        elif unit == 'elab_sandbox':
            self.directory = 'Sandbox/'+elab_name
            self.gpkg = elab_name+'.gpkg'
            if 'PRCLS' in layer:
                nodes_gdf = gpd.read_file(self.gpkg, layer='network_intersections')
                links_gdf = gpd.read_file(self.gpkg, layer='network_streets')
                cycling_gdf = gpd.read_file(self.gpkg, layer='network_cycling')
                if '2020' in layer:
                    self.nodes = nodes_gdf.loc[nodes_gdf['ctrld2020'] == 1]
                    self.links = links_gdf.loc[links_gdf['new'] == 0]
                    self.cycling = cycling_gdf.loc[cycling_gdf['year'] == '2020-01-01']
                    self.cycling['type'] = self.cycling['type2020']
                    self.cycling.reset_index(inplace=True)
                elif '2050' in layer:
                    self.nodes = nodes_gdf.loc[nodes_gdf['ctrld2050'] == 1]
                    self.links = links_gdf
                    self.cycling = cycling_gdf
                    self.cycling['type'] = cycling_gdf['type2050']
            self.properties = gpd.read_file(self.gpkg, layer=layer)
            self.properties.crs = {'init': 'epsg:26910'}

            # Reclassify land uses and create bedroom and bathroom columns
            uses = {'residential': ['RS_SF_D', 'RS_SF_A', 'RS_MF_L', 'RS_MF_H'],
                    'retail': ['CM', 'MX'],
                    'civic': ['CV'],
                    'green': ['OS']}
            new_uses = []
            index = list(self.properties.columns).index("LANDUSE")
            all_prim_uses = [item for sublist in list(uses.values()) for item in sublist]
            for row in self.properties.iterrows():
                for key, value in uses.items():
                    if row[1]['LANDUSE'] in value:
                        new_uses.append(key)
                if row[1]['LANDUSE'] not in all_prim_uses:
                    new_uses.append(row[1]['LANDUSE'])
            self.properties['n_use'] = new_uses
            self.properties['PRIMARY_ACTUAL_USE'] = self.properties['LANDUSE']
            self.properties['NUMBER_OF_BEDROOMS'] = 2
            self.properties['NUMBER_OF_BATHROOMS'] = 1

            # Define GeoDataFrame
            # gdf = gpd.GeoDataFrame(geometry=self.properties.unary_union.convex_hull)
            gdf = self.properties[['OBJECTID', 'geometry']]
            gdf.crs = {'init': 'epsg:26910'}
        else: gdf = None

        c_hull = gdf.geometry.unary_union.convex_hull
        if samples is not None:
            gdf = gdf.sample(samples)
            gdf.sindex()
        self.gdfs = {}
        buffers = {}
        for radius in service_areas:
            buffers[radius] = []

        if buffer_type == 'circular':
            # Buffer polygons for cross-scale data aggregation and output one GeoDataframe for each scale of analysis
            for row in gdf.iterrows():
                geom = row[1].geometry
                for radius in service_areas:
                    lda_buffer = geom.centroid.buffer(radius)
                    buffer_diff = lda_buffer.intersection(c_hull)
                    buffers[radius].append(buffer_diff)

        for radius in service_areas:
            self.gdfs['_r' + str(radius) + 'm'] = gpd.GeoDataFrame(geometry=buffers[radius], crs=gdf.crs)
            sindex = self.gdfs['_r' + str(radius) + 'm'].sindex
        self.params = {'gdf': gdf, 'service_areas': service_areas, 'layer': layer, 'backup': bckp}
        print(self.gdfs)
        print('Parameters set for ' + str(len(self.gdfs)) + ' spatial scales')
        return self.params

    def geomorph_indicators(self):
        # 'Topographical Unevenness'
        gdf = self.params['gdf']
        service_areas = self.params['service_areas']
        dict_of_dicts = {}
        try:
            for radius in service_areas:
                series = gdf.read_file(self.gpkg, layer='land_dissemination_area')[
                    'topo_unev_r' + str(radius) + 'm']
        except:
            start_time = timeit.default_timer()
            print('> Processing topographical unevenness')

            topo_unev = {}
            elevations = {}
            processed_keys = []
            for in_gdf, key in zip(self.gdfs.values(), self.gdfs.keys()):
                topo_unev[key] = []

                for pol, i in zip(in_gdf.geometry, enumerate(in_gdf.geometry)):
                    elevations[key] = []
                    geom_simp = pol.simplify(math.sqrt(pol.area) / 10, preserve_topology=False)
                    try:
                        bound_pts = geom_simp.exterior.coords
                    except:
                        bound_pts = geom_simp[0].exterior.coords
                    for pt in bound_pts:
                        # Reproject point to WGS84
                        pt_gdf = gpd.GeoDataFrame(geometry=[Point(pt)])
                        pt_gdf.crs = {'init': 'epsg:' + self.crs}
                        pt_gdf.to_crs({'init': 'epsg:4326'}, inplace=True)
                        # Define .hgt file to extract topographic data based on latitude and longitude
                        lon = str((int(pt_gdf.geometry.x[0]) * -1) + 1)
                        lat = str(int(pt_gdf.geometry.y[0]))
                        filename = 'N' + lat + 'W' + lon + '.hgt'
                        # Extract elevation data from .hgt file and add it to dictionary
                        elevation = self.elevation(self.directory+'Topography/' + filename,
                                                   lon=pt_gdf.geometry.x[0], lat=pt_gdf.geometry.y[0])
                        elevations[key].append(elevation)
                        elevations[key].sort()
                    print(elevations[key])
                    unev = elevations[key][len(elevations[key]) - 1] - elevations[key][0]
                    for key2 in processed_keys:
                        if topo_unev[key2][i[0]] > unev:
                            unev = topo_unev[key2][i[0]]
                    topo_unev[key].append(unev)
                    print(topo_unev)
                processed_keys.append(key)
            dict_of_dicts['topo_unev'] = topo_unev
            elapsed = round((timeit.default_timer() - start_time) / 60, 1)
            print('Topographical unevenness processed in ' + str(elapsed) + ' minutes')

        for key, value in dict_of_dicts.items():
            for key2, value2 in value.items():
                gdf[key + key2] = value2
        print(gdf)
        copyfile(self.gpkg, self.gpkg+'.bak')
        gdf.to_file(self.gpkg, layer='land_dissemination_area')
        return gdf

    def demographic_indicators(self):
        unit = 'lda'
        # WIP
        census_gdf = gpd.read_file(self.census_file)
        bound_gdf = gpd.read_file(self.gpkg, layer='land_census_subdivision')
        bound_gdf.crs = {'init': 'epsg:3348'}
        bound_gdf.to_crs({'init': 'epsg:4326'}, inplace=True)
        city_lda = census_gdf[census_gdf.within(bound_gdf.geometry[0])]
        print(city_lda)

        # Set up driver for web scraping
        options = Options()
        options.set_preference("browser.download.folderList", 1)
        options.set_preference("browser.download.manager.showWhenStarting", False)
        options.set_preference("browser.helperApps.neverAsk.saveToDisk", "text/csv")
        driver = webdriver.Firefox(executable_path=r'C:\WebDrivers\geckodriver.exe', options=options)
        driver.set_page_load_timeout(1)

        gdf = gpd.read_file('Databases/BC Assessment.gdb')
        print(gdf)

        # Population density (census 2016)
        if unit == 'lda':
            for da, csd in zip(city_lda['DAUID'], city_lda['CSDUID']):
                print(str(csd) + '-' + str(da))
                url = 'https://www12.statcan.gc.ca/census-recensement/2016/dp-pd/prof/details/' \
                      'download-telecharger/current-actuelle.cfm?Lang=E&Geo1=DA&Code1=' + da + '&Geo2=CSD&Code2=' \
                      + csd + '&B1=All&type=0&FILETYPE=CSV'
                print(url)
                driver.get(url)
        driver.close()
        return None

    def density_indicators(self):
        # Process 'Parcel Density', 'Dwelling Density', 'Bedroom Density', 'Bathroom Density', 'Retail Density'
        gdf = self.params['gdf']
        layer = self.params['layer']
        dict_of_dicts = {}

        print('> Processing spatial density indicators')
        start_time = timeit.default_timer()

        # Drop index columns from previous processing
        if 'index_right' in self.properties.columns:
            self.properties.drop('index_right', axis=1, inplace=True)
        if 'index_left' in self.properties.columns:
            self.properties.drop('index_left', axis=1, inplace=True)

        # Create empty dictionaries and lists
        parc_den = {}
        dwell_den = {}
        bed_den = {}
        bath_den = {}
        dest_den = {}
        dest_ct = {}
        dwell_ct = {}

        for geom, key in zip(self.gdfs.values(), self.gdfs.keys()):
            parc_den[key] = []
            dwell_den[key] = []
            bed_den[key] = []
            bath_den[key] = []
            dest_den[key] = []
            dest_ct[key] = []
            dwell_ct[key] = []

        # Iterate over GeoDataFrames
        for geom, key in zip(self.gdfs.values(), self.gdfs.keys()):

            if 'index_right' in geom.columns:
                geom.drop('index_right', axis=1, inplace=True)
            if 'index_left' in geom.columns:
                geom.drop('index_left', axis=1, inplace=True)

            jgdf = gpd.sjoin(geom, self.properties, how='right', op="intersects")
            for id in range(len(gdf)):
                fgdf = jgdf.loc[(jgdf['index_left'] == id)]
                if len(fgdf) == 0:
                    parc_den[key].append(0)
                    dwell_den[key].append(0)
                    bed_den[key].append(0)
                    bath_den[key].append(0)
                    dest_den[key].append(0)
                    dwell_ct[key].append(0)
                    dest_ct[key].append(0)

                else:
                    area = geom.loc[id].geometry.area

                    parc_gdf = fgdf.drop_duplicates(subset='geometry')
                    parc_den[key].append(len(parc_gdf)/area)

                    dwell_gdf = fgdf.loc[fgdf['n_use'] == 'residential']
                    dwell_den[key].append(len(dwell_gdf)/area)
                    dwell_ct[key].append(len(dwell_gdf))

                    bed_den[key].append(dwell_gdf['NUMBER_OF_BEDROOMS'].sum()/area)
                    bath_den[key].append(fgdf['NUMBER_OF_BATHROOMS'].sum()/area)

                    dest_gdf = fgdf.loc[(fgdf['n_use'] == 'retail') |
                                        (fgdf['n_use'] == 'office') |
                                        (fgdf['n_use'] == 'entertainment')]
                    dest_den[key].append(len(dest_gdf)/area)
                    dest_ct[key].append(len(dest_gdf))

        dict_of_dicts['parc_den'] = parc_den
        dict_of_dicts['dwell_ct'] = dwell_ct
        dict_of_dicts['dwell_den'] = dwell_den
        dict_of_dicts['bed_den'] = bed_den
        dict_of_dicts['bath_den'] = bath_den
        dict_of_dicts['dest_ct'] = dest_ct
        dict_of_dicts['dest_den'] = dest_den

        # Append all processed data to a single GeoDataFrame, backup and export
        for key, value in dict_of_dicts.items():
            for key2, value2 in value.items():
                gdf[key + key2] = value2
        if self.params['backup']:
            copyfile(self.gpkg, self.directory+'/ArchiveOSX/'+self.municipality+' - '+str(datetime.date.today())+'.gpkg')
        gdf.to_file(self.gpkg, layer=layer, driver='GPKG')
        elapsed = round((timeit.default_timer() - start_time) / 60, 1)
        return print('Density indicators processed in ' + str(elapsed) + ' minutes @ ' + str(datetime.datetime.now()))

    def diversity_indicators(self):
        # Process 'Land Use Diversity', 'Parcel Size Diversity', 'Dwelling Diversity'
        gdf = self.params['gdf']
        layer = self.params['layer']
        service_areas = self.params['service_areas']
        dict_of_dicts = {}

        print('> Processing spatial diversity indicators')
        start_time = timeit.default_timer()

        # Drop index columns from previous processing
        if 'index_right' in self.properties.columns:
            self.properties.drop('index_right', axis=1, inplace=True)
        if 'index_left' in self.properties.columns:
            self.properties.drop('index_left', axis=1, inplace=True)

        # Create empty dictionaries and lists
        use_div = {}
        dwell_div = {}
        parc_area_div = {}
        for geom, key in zip(self.gdfs.values(), self.gdfs.keys()):
            use_div[key] = []
            dwell_div[key] = []
            parc_area_div[key] = []

        # Iterate over GeoDataFrames
        for geom, key in zip(self.gdfs.values(), self.gdfs.keys()):

            if 'index_right' in geom.columns:
                geom.drop('index_right', axis=1, inplace=True)
            if 'index_left' in geom.columns:
                geom.drop('index_left', axis=1, inplace=True)

            jgdf = gpd.sjoin(geom, self.properties, how='right', op="intersects")
            for id in range(len(gdf)):
                fgdf = jgdf.loc[(jgdf['index_left'] == id)]
                if len(fgdf) == 0:
                    use_div[key].append(0)
                    dwell_div[key].append(0)
                    parc_area_div[key].append(0)
                else:
                    use_gdf = fgdf.loc[(fgdf['n_use'] == 'residential') |
                                       (fgdf['n_use'] == 'entertainment') |
                                       (fgdf['n_use'] == 'civic') |
                                       (fgdf['n_use'] == 'retail') |
                                       (fgdf['n_use'] == 'office')]
                    use_div[key].append(shannon_div(use_gdf, 'n_use'))

                    res_gdf = fgdf.loc[(fgdf['n_use'] == 'residential')]
                    dwell_div[key].append(shannon_div(res_gdf, 'PRIMARY_ACTUAL_USE'))

                    parcel_gdf = fgdf.drop_duplicates(subset=['geometry'])
                    parcel_gdf['area'] = parcel_gdf.geometry.area
                    parcel_gdf.loc[parcel_gdf['area'] < 400, 'area_group'] = '<400'
                    parcel_gdf.loc[(parcel_gdf['area'] > 400) & (parcel_gdf['area'] < 800), 'area_group'] = '400><800'
                    parcel_gdf.loc[(parcel_gdf['area'] > 800) & (parcel_gdf['area'] < 1600), 'area_group'] = '800><1600'
                    parcel_gdf.loc[(parcel_gdf['area'] > 1600) & (parcel_gdf['area'] < 3200), 'area_group'] = '1600><3200'
                    parcel_gdf.loc[(parcel_gdf['area'] > 3200) & (parcel_gdf['area'] < 6400), 'area_group'] = '3200><6400'
                    parcel_gdf.loc[parcel_gdf['area'] > 6400, 'area_group'] = '>6400'
                    parc_area_div[key].append(shannon_div(parcel_gdf, 'area_group'))

        dict_of_dicts['use_div'] = use_div
        dict_of_dicts['dwell_div'] = dwell_div
        dict_of_dicts['parc_area_div'] = parc_area_div

        # Append all processed data to a single GeoDataFrame, backup and export
        for key, value in dict_of_dicts.items():
            for key2, value2 in value.items():
                gdf[key + key2] = value2
        if self.params['backup']:
            copyfile(self.gpkg, self.directory+'/ArchiveOSX/'+self.municipality+' - '+str(datetime.date.today())+'.gpkg')
        gdf.to_file(self.gpkg, layer=layer)
        elapsed = round((timeit.default_timer() - start_time) / 60, 1)
        return print('Diversity indicators processed in ' + str(elapsed) + ' minutes @ ' + str(datetime.datetime.now()))

    def street_network_indicators(self, net_simperance=10):
        # Define GeoDataframe sample_layer unit
        gdf = self.params['gdf']
        layer = self.params['layer']
        service_areas = self.params['service_areas']
        dict_of_dicts = {}

        # 'Intersection Density', 'Link-node Ratio', 'Network Density', 'Average Street Length'
        start_time = timeit.default_timer()
        print('> Processing general network indicators')
        intrs_den = {}
        linkn_rat = {}
        netw_den = {}
        strt_len = {}
        for geom, key in zip(self.gdfs.values(), self.gdfs.keys()):
            intrs_den[key] = []
            linkn_rat[key] = []
            netw_den[key] = []
            strt_len[key] = []
            exceptions = []
            for pol in geom.geometry:
                nodes_w = self.nodes[self.nodes.geometry.within(pol)]
                try:
                    nodes_w = nodes_w.geometry.buffer(net_simperance).unary_union
                    len_nodes_w = len(nodes_w)
                    if len(nodes_w) == 0:
                        len_nodes_w = 1
                except:
                    exceptions.append('exception')
                    len_nodes_w = 1
                intrs_den[key].append(round(len_nodes_w / (pol.area / 10000), 2))
                edges_w = self.links[self.links.geometry.within(pol)]
                len_edges_w = len(edges_w)
                if len(edges_w) == 0:
                    len_edges_w = 1
                edges_w_geom_length = edges_w.geometry.length
                if len(edges_w_geom_length) == 0:
                    edges_w_geom_length = [1, 1]
                linkn_rat[key].append(round(len_edges_w / len_nodes_w, 2))
                netw_den[key].append(round(sum(edges_w_geom_length) / (pol.area / 10000), 5))
                strt_len[key].append(round(sum(edges_w_geom_length) / len(edges_w_geom_length), 2))
            print('Network iterations at the ' + key + ' scale finished with a total of ' + str(
                  len(exceptions)) + ' exceptions')
        dict_of_dicts['intrs_den'] = intrs_den
        dict_of_dicts['linkn_rat'] = linkn_rat
        dict_of_dicts['netw_den'] = netw_den
        dict_of_dicts['strt_len'] = strt_len
        elapsed = round((timeit.default_timer() - start_time) / 60, 1)
        print('General network indicators processed in ' + str(elapsed) + ' minutes')

        for key, value in dict_of_dicts.items():
            for key2, value2 in value.items():
                gdf[key + key2] = value2
        copyfile(self.gpkg, self.gpkg+'.bak')
        gdf.to_file(self.gpkg, layer=layer)
        print('Processing finished @ ' + str(datetime.datetime.now()))
        return None

    def cycling_network_indicators(self):
        # Read file and pre-process geometry according to its type
        if str(type(self.cycling.geometry[0])) != "<class 'shapely.geometry.polygon.Polygon'>":
            print('> Geometry is not polygon, buffering')
            self.cycling.geometry = self.cycling.buffer(40)

        gdf = self.params['gdf']
        layer = self.params['layer']

        if 'index_left' in gdf.columns:
            gdf.drop(['index_left'], axis=1, inplace=True)
        if 'index_right' in gdf.columns:
            gdf.drop(['index_right'], axis=1, inplace=True)

        dict_of_dicts = {}
        start_time = timeit.default_timer()
        print('> Processing cycling network indicators')

        onstreet = {}
        offstreet = {}
        informal = {}
        all_cycl = {}
        onstreet_gdf = self.cycling[self.cycling['type'] == 'onstreet']
        offstreet_gdf = self.cycling[self.cycling['type'] == 'offstreet']
        informal_gdf = self.cycling[self.cycling['type'] == 'informal']

        for geom, key in zip(self.gdfs.values(), self.gdfs.keys()):
            onstreet[key] = []
            offstreet[key] = []
            informal[key] = []
            all_cycl[key] = []
            for pol in geom.geometry:
                onstreet_w = onstreet_gdf[onstreet_gdf.geometry.within(pol)]
                offstreet_w = offstreet_gdf[offstreet_gdf.geometry.within(pol)]
                informal_w = informal_gdf[informal_gdf.geometry.within(pol)]
                all_cycl_w = gdf[gdf.geometry.within(pol)]
                if len(onstreet_w.geometry) == 0: onstreet[key].append(0)
                else: onstreet[key].append(sum(onstreet_w.geometry.area))
                if len(offstreet_w.geometry) == 0: offstreet[key].append(0)
                else: offstreet[key].append(sum(offstreet_w.geometry.area))
                if len(informal_w.geometry) == 0: informal[key].append(0)
                else: informal[key].append(sum(informal_w.geometry.area))
                if len(all_cycl_w.geometry) == 0: all_cycl[key].append(0)
                else: all_cycl[key].append(sum(all_cycl_w.geometry.area))
        print(all_cycl)

        dict_of_dicts['cycl_onstreet'] = onstreet
        dict_of_dicts['cycl_offstreet'] = offstreet
        dict_of_dicts['cycl_informal'] = informal
        dict_of_dicts['all_cycl'] = all_cycl

        for key, value in dict_of_dicts.items():
            for key2, value2 in value.items():
                gdf[key + key2] = value2
        if self.params['backup']:
            copyfile(self.gpkg, self.directory+'ArchiveOSX/'+self.municipality+' - '+str(datetime.date.today())+'.gpkg')
        gdf.to_file(self.gpkg, layer=layer)

        elapsed = round((timeit.default_timer() - start_time) / 60, 1)
        return print('Cycling network indicators processed in ' + str(elapsed) + ' minutes')

    # Process results
    def p_values(self, df, x_features, y_features):
        """
        Reference: https://towardsdatascience.com/feature-selection-correlation-and-p-value-da8921bfb3cf
        """

        # Pre process variables
        y_gdf = df[y_features]
        y_gdf.fillna(0, inplace=True)
        mask = [(s != 0) for s in y_gdf.sum(axis=1)]
        y_gdf = y_gdf.loc[mask, :]
        x_gdf = df[x_features]
        x_gdf.fillna(0, inplace=True)

        # Get linear correlations
        corr = x_gdf.corr()
        sns.heatmap(corr)
        plt.savefig('Correlation.png')

        c_gdf = pd.concat([x_gdf, y_gdf], axis=1)
        c_gdf = c_gdf.corr().loc[:, ['walk', 'bike', 'drive', 'bus']].drop(['walk', 'bike', 'drive', 'bus'], axis=0)
        c_gdf['sum'] = c_gdf.abs().sum(axis=1)
        c_gdf = c_gdf.sort_values(by=['sum'], ascending=False)
        c_gdf.to_csv(f'Regression/{self.municipality} - Correlation.csv')

        c_gdf.abs()

        """
        # Drop correlations higher than x%
        columns = np.full((corr.shape[0],), True, dtype=bool)
        for i in range(corr.shape[0]):
            for j in range(i + 1, corr.shape[0]):
                if corr.iloc[i, j] >= 0.9:
                    if columns[j]:
                        columns[j] = False
        """

        selected_columns = x_gdf.columns  # [columns]
        x_gdf = x_gdf[selected_columns]
        x_gdf = x_gdf.iloc[y_gdf.index]

        # Calculate p-values
        x = x_gdf.values
        y = y_gdf.values
        p = pd.DataFrame()
        p['feature'] = selected_columns

        """
        def backwardElimination(x, Y, sl, columns):
            num_vars = len(x[0])
            for i in range(0, num_vars):
                regressor_ols = sm.OLS(Y, x).fit()
                max_var = max(regressor_ols.pvalues).astype(float)
                if max_var > sl:
                    for j in range(0, num_vars - i):
                        if regressor_ols.pvalues[j].astype(float) == max_var:
                            x = np.delete(x, j, 1)
                            columns = np.delete(columns, j)
                regressor_ols.summary()
            return x, columns
        """

        # Select columns based on p-value
        for i, col in enumerate(y_gdf.columns):
            SL = 0.05
            regressor_ols = sm.OLS(y.transpose()[i], x).fit()

            # with open(f'Regression/{datetime.datetime.now()}_{col}.txt', 'w') as file:
            #     file.write(str(regressor_ols.summary()))

            p[f'{col}_pv'] = regressor_ols.pvalues

        p = p.set_index('feature')
        # p = p.apply(lambda x: [y if y < 0.15 else np.nan for y in x])
        p = p.dropna(axis=0, how='all')
        p = p.loc[c_gdf.index, :]
        p.to_csv(f'Regression/{self.municipality} - P-values.csv')

        # data_modeled, selected_columns = backwardElimination(
        #     x_gdf.values,
        #     y_gdf[col].values,
        #     SL, selected_columns
        # )

        # data = pd.DataFrame(data=data_modeled, columns=selected_columns)
        # data.to_csv(f'Regression/{self.municipality} - {col}.csv')

        # selected_columns = selected_columns[1:].values

        # # Select n highest p-values for each Y
        # highest = pd.DataFrame()
        # for i, col in enumerate(y_gdf.columns):
        #     srtd = p.sort_values(by=f'{col}_pv', ascending=False)
        #     highest[f'{col}'] = list(srtd.head(3)['feature'])

        return

    def network_report(self):
        nodes_gdf = gpd.read_file(self.gpkg, layer='network_nodes')
        links_gdf = gpd.read_file(self.gpkg, layer='network_links')

        # Setup directory parameters
        save_dir = f"{self.directory}Reports/"
        if 'Reports' in os.listdir(self.directory): pass
        else: os.mkdir(save_dir)
        if self.municipality in os.listdir(save_dir): pass
        else: os.mkdir(f"{save_dir}{self.municipality}")

        # Calculate boundary area
        df = pd.DataFrame()
        try:
            self.boundary = self.boundary.to_crs(3157)
            bounds = self.boundary.area[0]/10000
        except:
            print(f'No boundary found, using convex hull')
            nodes_gdf.crs = 3157
            links_gdf.crs = 3157
            bounds = links_gdf.unary_union.convex_hull.area/10000
        print(f'Area: {bounds} ha')

        links_gdf_bf = gpd.GeoDataFrame(geometry=[links_gdf.buffer(1).unary_union])
        nodes_gdf_bf = gpd.GeoDataFrame(geometry=[nodes_gdf.buffer(7).unary_union])
        links_gdf_sf = gpd.GeoDataFrame(geometry=[l for l in gpd.overlay(links_gdf_bf, nodes_gdf_bf, how='difference').geometry[0]])

        # Calculate basic network indicators
        print(f"> Calculating basic network stats")
        df['Area'] = [format(bounds, '.2f')]
        df['Node count'] = [format(len(nodes_gdf), '.2f')]
        df['Link count'] = [format(len(links_gdf_sf), '.2f')]
        df['Node Density (nodes/ha)'] = [format(len(nodes_gdf)/bounds, '.2f')]
        df['Link Density (links/ha)'] = [format(len(links_gdf_sf)/bounds, '.2f')]
        df['Link-Node Ratio (count)'] = [format(len(links_gdf_sf)/len(nodes_gdf), '.2f')]
        df['Average Link Length (meters)'] = [format(sum([(ln.area) for ln in links_gdf_sf.geometry])/len(links_gdf_sf), '.2f')]
        df = df.transpose()
        df.index.name = 'Indicator'
        df.columns = ['Measure']

        # Define image properties
        fig, (ax0, ax1) = plt.subplots(2, 1, gridspec_kw={'height_ratios': [6, 1]})
        fig.set_size_inches(7.5, 7.5)
        ax0.axis('off')
        ax1.axis('off')

        # Plot map and table
        ax0.set_title(f'Network Indicators - {self.municipality}')
        links_gdf.buffer(4).plot(ax=ax0, facecolor='black', linewidth=0.5, linestyle='solid')
        nodes_gdf.buffer(8).plot(ax=ax0, edgecolor='black', facecolor='white', linewidth=0.5, linestyle='solid')
        ax1.table(
            cellText=df.values,
            colLabels=df.columns,
            colWidths=[0.1],
            rowLabels=df.index,
            loc='right',
            edges='horizontal')

        # Setup and save figure
        plt.savefig(f"{save_dir}{self.municipality}.png", dpi=300)

        # Plot centrality measures if exists
        if 'betweenness' in links_gdf.columns:
            links_gdf.plot(column='betweenness', cmap='viridis_r', legend=True)
            fig.set_size_inches(7.5, 7.5)
            plt.axis('off')
            plt.title(f'Betweenness - {self.municipality}')
            plt.savefig(f"{save_dir}{self.municipality}_bt.png", dpi=300)

        df['Measure'] = pd.to_numeric(df['Measure'])
        print(f"Report successfully saved at {self.directory}")
        return df

    def export_map(self):
        
        # Process geometry
        boundaries = self.DAs.geometry.boundary
        centroids = gpd.GeoDataFrame(geometry=self.DAs.geometry.centroid)
        buffers = {'radius': [], 'geometry': []}
        for radius in self.params['service_areas']:
            for geom in centroids.geometry.buffer(radius):
                buffers['radius'].append(radius)
                buffers['geometry'].append(geom)
        buffers_gdf = gpd.GeoDataFrame(buffers)
        buffer_bounds = gpd.GeoDataFrame(geometry=buffers_gdf['geometry'].boundary)
        buffer_bounds['radius'] = buffers_gdf['radius']

        COLOR_MAP = 'viridis'
        ALPHA = 0.05

        cmap = cm.get_cmap(COLOR_MAP)
        colormap_r = ListedColormap(cmap.colors[::-1])

        # Plot geometry
        fig, ax = plt.subplots(1, 1)
        buffer_bounds.plot(ax=ax, column='radius', colormap=COLOR_MAP, alpha=ALPHA*2)
        boundaries.plot(ax=ax, color='black', linewidth=0.2, linestyle='solid', alpha=0.6)
        centroids.plot(ax=ax, color='#88D2D5', markersize=0.2)
        plt.axis('off')
        plt.savefig('Diagrams/'+self.municipality+' - Mobility Diagram.png', dpi=600)

        return self

    def linear_correlation_lda(self):
        gdf = gpd.read_file(self.gpkg, layer='land_dissemination_area')
        gdf = gdf.loc[gdf.geometry.area < 7000000]
        r = gdf.corr(method='pearson')
        r.to_csv(self.directory + self.municipality + '_r.csv')
        print(r)

    def export_destinations(self):
        dest_gdf = self.properties.loc[(self.properties['n_use'] == 'retail') |
                                    (self.properties['n_use'] == 'office') |
                                    (self.properties['n_use'] == 'entertainment')]
        dest_gdf['geometry'] = dest_gdf.geometry.centroid
        dest_gdf.drop_duplicates('geometry')
        dest_gdf.to_file(self.directory+'Shapefiles/'+self.municipality+' - Destinations.shp', driver='ESRI Shapefile')
        return self

    def export_parcels(self):
        gdf = self.properties
        gdf.to_file('Shapefiles/' + self.params['layer'], driver='ESRI Shapefile')
        for col in gdf.columns:
            if str(type(gdf[col][0])) == "<class 'numpy.float64'>" or str(type(gdf[col][0])) == "<class 'numpy.int64'>" or col == "LANDUSE":
                if sum(gdf[col]) == 0:
                    gdf.drop(col, inplace=True, axis=1)
                    print(col + ' column removed')
        gdf.to_file('Shapefiles/'+self.params['layer']+'_num', driver='ESRI Shapefile')
        return self

    def export_databases(self):
        layers = ['network_streets', 'land_dissemination_area', 'land_assessment_fabric']
        directory = '/Users/nicholasmartino/Desktop/temp/'
        for layer in layers:
            print('Exporting layer: '+layer)
            gdf = gpd.read_file(self.gpkg, layer=layer)
            gdf.to_file(directory+self.municipality+' - '+layer+'.shp', driver='ESRI Shapefile')
        return self


if __name__ == '__main__':
    BUILD_REAL_NETWORK = False
    BUILD_PROXY_NETWORK = False
    NETWORK_STATS = False
    VERSIONS = ["", '2']

    osm = GeoBoundary(municipality='Victoria, British Columbia', crs=26910)
    osm.update_databases(bound=False, net=False)
    osm.centrality()

    real_auto = GeoBoundary(municipality=f'Hillside Quadra', crs=26910)
    if BUILD_REAL_NETWORK: real_auto.network_from_polygons(
        filepath="/Users/nicholasmartino/GoogleDrive/Geospatial/Databases/Hillside Quadra.gpkg",
        layer='land_blocks', scale_factor=0.84, buffer_radius=11, max_linters=0.40)

    for VERSION in VERSIONS:
        proxy = GeoBoundary(municipality=f'Hillside Quadra Proxy{VERSION}', crs=26910)
        if BUILD_PROXY_NETWORK: proxy.network_from_polygons(
            filepath=f"/Users/nicholasmartino/GoogleDrive/Geospatial/Databases/Hillside Quadra Proxy{VERSION}.gpkg",
            layer='land_parcels', scale_factor=0.80, buffer_radius=10, max_linters=0.25, remove_islands=False)

    if NETWORK_STATS:
        real_auto.centrality()
        rrep = real_auto.network_report()
        for VERSION in VERSIONS:
            proxy = GeoBoundary(municipality=f'Hillside Quadra Proxy{VERSION}', crs=26910)
            proxy.centrality()
        prep = proxy.network_report()
        print(rrep - prep)
        print("Done")