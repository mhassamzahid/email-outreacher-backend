"""The example email library: emails in the voice we want, linked to ICPs. The email and scoring
prompts get the newest ones for the lead's ICP as {{examples.samples}} (how many is a setting)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from crud import EmailRepo, ExampleRepo, FeedbackRepo
from dependencies import get_email_repo, get_example_repo, get_feedback_repo, require_manager, require_workspace_id
from schemas import ExampleEmailIn, ExampleEmailOut, SaveAsExampleIn

router = APIRouter(prefix="/examples", tags=["examples"])


def _clean(body: ExampleEmailIn) -> dict:
    values = body.model_dump()
    values["subject"], values["body"], values["note"] = body.subject.strip(), body.body.strip(), body.note.strip()
    values["icp_ids"] = [] if body.all_icps else list(dict.fromkeys(body.icp_ids))
    if not values["body"]:
        raise HTTPException(status_code=422, detail="An example needs a body")
    if not values["all_icps"] and not values["icp_ids"]:
        raise HTTPException(status_code=422, detail="Pick the ICPs it's for, or all ICPs")
    return values


async def _get(examples: ExampleRepo, workspace_id: UUID, example_id: UUID):
    row = await examples.get(workspace_id, example_id)
    if not row:
        raise HTTPException(status_code=404, detail="Example not found")
    return row


@router.get("", response_model=list[ExampleEmailOut])
async def list_examples(
    workspace_id: UUID = Depends(require_workspace_id), examples: ExampleRepo = Depends(get_example_repo)
) -> list[ExampleEmailOut]:
    return [ExampleEmailOut.model_validate(e) for e in await examples.list(workspace_id)]


@router.post("", response_model=ExampleEmailOut, status_code=201, dependencies=[Depends(require_manager)])
async def create_example(
    body: ExampleEmailIn,
    workspace_id: UUID = Depends(require_workspace_id),
    examples: ExampleRepo = Depends(get_example_repo),
) -> ExampleEmailOut:
    if not await examples.workspace_exists(workspace_id):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ExampleEmailOut.model_validate(await examples.create(workspace_id, {**_clean(body), "source": "manual"}))


@router.put("/{example_id}", response_model=ExampleEmailOut, dependencies=[Depends(require_manager)])
async def update_example(
    example_id: UUID,
    body: ExampleEmailIn,
    workspace_id: UUID = Depends(require_workspace_id),
    examples: ExampleRepo = Depends(get_example_repo),
) -> ExampleEmailOut:
    row = await _get(examples, workspace_id, example_id)
    return ExampleEmailOut.model_validate(await examples.update(row, _clean(body)))


@router.delete("/{example_id}", status_code=204, dependencies=[Depends(require_manager)])
async def delete_example(
    example_id: UUID,
    workspace_id: UUID = Depends(require_workspace_id),
    examples: ExampleRepo = Depends(get_example_repo),
) -> Response:
    await examples.delete(await _get(examples, workspace_id, example_id))
    return Response(status_code=204)


@router.post("/from-email/{email_id}", response_model=ExampleEmailOut, status_code=201, dependencies=[Depends(require_manager)])
async def save_email_as_example(
    email_id: UUID,
    body: SaveAsExampleIn,
    workspace_id: UUID = Depends(require_workspace_id),
    emails: EmailRepo = Depends(get_email_repo),
    feedback: FeedbackRepo = Depends(get_feedback_repo),
    examples: ExampleRepo = Depends(get_example_repo),
) -> ExampleEmailOut:
    """Copy a generated email (with your edits) into the library, for its lead's ICP. The note defaults
    to the email's feedback note."""
    found = await emails.get(workspace_id, email_id)
    if not found:
        raise HTTPException(status_code=404, detail="Email not found")
    email, lead = found
    if lead.lead_intel_icp_id is None:
        raise HTTPException(status_code=422, detail="This email's lead has no ICP to link the example to")
    rating = (await feedback.for_emails([email_id])).get(email_id)
    row = await examples.create(
        workspace_id,
        {
            "subject": email.subject, "body": (email.edited_body or email.body).strip(),
            "note": body.note.strip() or (rating.note if rating else ""), "all_icps": False,
            "icp_ids": [lead.lead_intel_icp_id], "source": "generated", "source_email_id": email.id,
        },
    )  # fmt: skip
    return ExampleEmailOut.model_validate(row)
