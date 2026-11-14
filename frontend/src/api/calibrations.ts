import {request} from './client';
import {CalibrationBatch,RevisionSummary} from '../types/calibration';
export const getBatches=(libraryId:number)=>request<CalibrationBatch[]>(`/calibrations/libraries/${libraryId}/batches`);
export const getBatch=(id:number)=>request<CalibrationBatch>(`/calibrations/batches/${id}`);
export const createBatch=(libraryId:number,data:{title:string;endmembers:{code:string;name:string;spectrum:number[]}[]})=>request<CalibrationBatch>(`/calibrations/libraries/${libraryId}/batches`,{method:'POST',body:JSON.stringify(data)});
export const publishBatch=(id:number)=>request<CalibrationBatch>(`/calibrations/batches/${id}/publish`,{method:'POST'});
export const retryBatch=(id:number)=>request<CalibrationBatch>(`/calibrations/batches/${id}/retry`,{method:'POST'});
export const getRevisions=(runId:number)=>request<RevisionSummary[]>(`/calibrations/runs/${runId}/revisions`);
export const getRevisionRun=(runId:number,resultId:number)=>request(`/calibrations/runs/${runId}/revisions/${resultId}`);
