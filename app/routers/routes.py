"""
Routes for GTFS data and transit route management
Integrates with pfaedle for precise route shape generation
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import (
    get_db, User, GTFSDataset, TransitRoute, RouteStop, RouteShape
)
from app.schemas import (
    GTFSDatasetCreate, GTFSDatasetResponse, 
    TransitRouteResponse, TransitRouteDetailResponse,
    RouteStopResponse, RouteShapeResponse,
    GTFSProcessingRequest, GTFSProcessingResult,
    SwissTransportCompany
)
from app.core.auth import verify_jwt_token
from app.services.gtfs_processor import GTFSProcessor, SWISS_TRANSPORT_SOURCES

router = APIRouter()

@router.get("/swiss-transport-sources", response_model=List[SwissTransportCompany])
async def get_swiss_transport_sources():
    """
    Get available Swiss public transport data sources
    
    Returns preset configurations for major Swiss transport operators:
    - SBB (Swiss Federal Railways) - Complete Swiss timetable
    - PostBus - Swiss postal bus network
    - ZVV - Zurich metropolitan transport
    """
    sources = []
    for key, source in SWISS_TRANSPORT_SOURCES.items():
        sources.append(SwissTransportCompany(
            name=source['name'],
            gtfs_url=source['gtfs_url'],
            osm_region=source['osm_region'],
            description=source['description']
        ))
    return sources

@router.post("/gtfs-datasets", response_model=GTFSDatasetResponse)
async def create_gtfs_dataset(
    dataset_data: GTFSDatasetCreate,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new GTFS dataset for processing
    
    This creates a dataset record but doesn't start processing yet.
    Use the process endpoint to download and process the data with pfaedle.
    """
    
    # Check if dataset name already exists for this company
    existing = await db.execute(
        select(GTFSDataset).where(
            GTFSDataset.company_id == current_user.company_id,
            GTFSDataset.dataset_name == dataset_data.dataset_name
        )
    )
    
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dataset with this name already exists for your company"
        )
    
    dataset = GTFSDataset(
        company_id=current_user.company_id,
        dataset_name=dataset_data.dataset_name,
        gtfs_url=dataset_data.gtfs_url,
        osm_extract_url=dataset_data.osm_extract_url
    )
    
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    
    return dataset

@router.post("/gtfs-datasets/{dataset_id}/process-swiss", response_model=GTFSDatasetResponse)
async def process_swiss_gtfs_dataset(
    dataset_id: str,
    source_key: str = "sbb",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Download and process Swiss transport data with pfaedle
    
    **Parameters:**
    - `source_key`: Which Swiss transport source to use (sbb, postbus, zurich)
    
    This endpoint:
    1. Downloads GTFS data from the specified Swiss transport operator
    2. Downloads corresponding OSM data for Switzerland
    3. Processes the GTFS with pfaedle to enhance route shapes
    4. Parses and stores routes, stops, and shapes in the database
    
    **Processing happens in background** - check the dataset status for progress.
    """
    
    # Verify dataset exists and belongs to user's company
    result = await db.execute(
        select(GTFSDataset).where(
            GTFSDataset.id == dataset_id,
            GTFSDataset.company_id == current_user.company_id
        )
    )
    dataset = result.scalar_one_or_none()
    
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found"
        )
    
    if source_key not in SWISS_TRANSPORT_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown source: {source_key}. Available: {list(SWISS_TRANSPORT_SOURCES.keys())}"
        )
    
    # Start background processing
    background_tasks.add_task(
        _process_dataset_background,
        str(dataset_id),
        str(current_user.company_id),
        source_key
    )
    
    # Update dataset status
    dataset.status = "downloading"
    await db.commit()
    await db.refresh(dataset)
    
    return dataset

async def _process_dataset_background(dataset_id: str, company_id: str, source_key: str):
    """Background task for processing GTFS dataset"""
    from app.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        processor = GTFSProcessor(db)
        try:
            await processor.download_and_process_swiss_data(company_id, source_key)
        except Exception as e:
            # Update dataset status to failed
            result = await db.execute(
                select(GTFSDataset).where(GTFSDataset.id == dataset_id)
            )
            dataset = result.scalar_one_or_none()
            if dataset:
                dataset.status = "failed"
                await db.commit()

@router.get("/gtfs-datasets", response_model=List[GTFSDatasetResponse])
async def get_gtfs_datasets(
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get all GTFS datasets for the user's company"""
    
    result = await db.execute(
        select(GTFSDataset).where(
            GTFSDataset.company_id == current_user.company_id
        ).order_by(GTFSDataset.created_at.desc())
    )
    
    return result.scalars().all()

@router.get("/transit-routes", response_model=List[TransitRouteResponse])
async def get_transit_routes(
    dataset_id: Optional[str] = None,
    route_type: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get transit routes for the user's company
    
    **Parameters:**
    - `dataset_id`: Filter by specific GTFS dataset
    - `route_type`: Filter by GTFS route type (0=tram, 1=subway, 2=rail, 3=bus, etc.)
    - `skip`: Pagination offset
    - `limit`: Number of routes to return (max 100)
    """
    
    query = select(TransitRoute).where(
        TransitRoute.company_id == current_user.company_id
    )
    
    if dataset_id:
        query = query.where(TransitRoute.gtfs_dataset_id == dataset_id)
    
    if route_type is not None:
        query = query.where(TransitRoute.route_type == route_type)
    
    query = query.order_by(TransitRoute.route_short_name).offset(skip).limit(limit)
    
    result = await db.execute(query)
    return result.scalars().all()

@router.get("/transit-routes/{route_id}", response_model=TransitRouteDetailResponse)
async def get_transit_route_detail(
    route_id: str,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get detailed information for a specific transit route including stops and shapes
    """
    
    result = await db.execute(
        select(TransitRoute)
        .options(
            selectinload(TransitRoute.route_stops),
            selectinload(TransitRoute.route_shapes)
        )
        .where(
            TransitRoute.id == route_id,
            TransitRoute.company_id == current_user.company_id
        )
    )
    
    route = result.scalar_one_or_none()
    
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found"
        )
    
    return route

@router.get("/transit-routes/{route_id}/stops", response_model=List[RouteStopResponse])
async def get_route_stops(
    route_id: str,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get all stops for a specific transit route, ordered by sequence"""
    
    # Verify route belongs to user's company
    route_result = await db.execute(
        select(TransitRoute).where(
            TransitRoute.id == route_id,
            TransitRoute.company_id == current_user.company_id
        )
    )
    
    if not route_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found"
        )
    
    result = await db.execute(
        select(RouteStop).where(
            RouteStop.transit_route_id == route_id
        ).order_by(RouteStop.stop_sequence)
    )
    
    return result.scalars().all()

@router.get("/transit-routes/{route_id}/shapes", response_model=List[RouteShapeResponse])
async def get_route_shapes(
    route_id: str,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get route shapes (geographic paths) for a specific transit route
    
    These shapes are enhanced by pfaedle processing and contain precise
    geographic coordinates for the route path.
    """
    
    # Verify route belongs to user's company
    route_result = await db.execute(
        select(TransitRoute).where(
            TransitRoute.id == route_id,
            TransitRoute.company_id == current_user.company_id
        )
    )
    
    if not route_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found"
        )
    
    result = await db.execute(
        select(RouteShape).where(
            RouteShape.transit_route_id == route_id
        )
    )
    
    return result.scalars().all()

@router.get("/route-types")
async def get_gtfs_route_types():
    """
    Get GTFS route type definitions
    
    These are the standard GTFS route type codes used to categorize
    different types of public transport.
    """
    return {
        "standard_types": {
            0: "Tram, Streetcar, Light rail",
            1: "Subway, Metro", 
            2: "Rail (intercity or long-distance)",
            3: "Bus",
            4: "Ferry",
            5: "Cable tram",
            6: "Aerial lift, Gondola",
            7: "Funicular",
            11: "Trolleybus",
            12: "Monorail"
        },
        "swiss_specific": {
            "description": "In Switzerland, route type 3 (bus) is most common for local transport",
            "typical_operators": {
                "SBB": [1, 2],  # Trains
                "PostBus": [3],  # Buses
                "ZVV": [0, 1, 3]  # Trams, trains, buses
            }
        }
    } 